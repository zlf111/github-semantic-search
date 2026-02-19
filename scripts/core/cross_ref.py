"""Cross-reference engine — links Issues ↔ PRs ↔ Commits.

Extracts #N references from PR bodies, commit messages, and issue bodies,
then builds a complete cross-reference map across all search result types.
Supports: Issue↔PR, PR↔PR, Commit→PR, Commit→Issue.
"""

import logging
import os
import re
from collections import defaultdict

log = logging.getLogger("gss.crossref")

# Patterns that link a PR to an Issue (strong link)
_FIX_PATTERNS = re.compile(
    r"(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)",
    re.IGNORECASE,
)

# Generic #N reference in any text
_ISSUE_REF = re.compile(r"(?<!\w)#(\d+)(?!\w)")


def _extract_refs(text: str, min_num: int = 10, max_num: int = 99999) -> set[int]:
    """Extract all #N references from text, filtering to reasonable range."""
    if not text:
        return set()
    return {int(m.group(1)) for m in _ISSUE_REF.finditer(text)
            if min_num < int(m.group(1)) < max_num}


def build_cross_references(
    issue_results: dict | None = None,
    pr_results: dict | None = None,
    commit_results: dict | None = None,
) -> dict:
    """Build a comprehensive cross-reference map across search types.

    Detects these reference types:
      - Issue ↔ PR  (via fixes/closes/resolves keywords, linked_issues, or #N)
      - PR → PR     (via #N in PR body/title referencing another PR in results)
      - Commit → PR (via (#N) in commit message referencing a PR in results)
      - Commit → Issue (via #N in commit message referencing an issue in results)

    Returns a dict with link maps, edge list, and stats.
    """
    issue_nums = set(issue_results.keys()) if issue_results else set()
    pr_nums = set(pr_results.keys()) if pr_results else set()

    # All known numbers in our results
    all_known = issue_nums | pr_nums

    # Edge list: (source_type, source_id, target_type, target_id, relation)
    edges: list[tuple] = []

    # --- PR body → Issue / PR links ---
    if pr_results:
        for pr in pr_results.values():
            body = pr.body or ""
            title = pr.title or ""
            text = title + " " + body

            # Strong links: fixes/closes/resolves
            fix_refs = set()
            for m in _FIX_PATTERNS.finditer(body):
                fix_refs.add(int(m.group(1)))

            # Pre-extracted linked_issues
            linked = set(pr.linked_issues) if pr.linked_issues else set()
            fix_refs |= linked

            # All #N refs in body + pre-extracted linked_issues
            all_refs = _extract_refs(text) - {pr.number}
            all_refs |= linked  # ensure linked_issues are always included

            # Classify each reference
            for ref in all_refs:
                if ref in issue_nums:
                    rel = "fixes" if ref in fix_refs else "refs"
                    edges.append(("pr", pr.number, "issue", ref, rel))
                elif ref in pr_nums and ref != pr.number:
                    edges.append(("pr", pr.number, "pr", ref, "refs"))

    # --- Commit message → Issue / PR links ---
    if commit_results:
        for commit in commit_results.values():
            msg = commit.message or ""
            refs = _extract_refs(msg)

            for ref in refs:
                if ref in issue_nums:
                    edges.append(("commit", commit.sha[:10], "issue", ref, "refs"))
                elif ref in pr_nums:
                    edges.append(("commit", commit.sha[:10], "pr", ref, "refs"))

    # --- Issue body → PR links (less common but possible) ---
    if issue_results:
        for issue in issue_results.values():
            body = issue.body or ""
            refs = _extract_refs(body) - {issue.number}
            for ref in refs:
                if ref in pr_nums:
                    edges.append(("issue", issue.number, "pr", ref, "refs"))

    # Deduplicate edges
    edges = list(set(edges))

    # Build indexed maps for easy lookup
    issue_to_prs: dict[int, list[int]] = defaultdict(list)
    pr_to_issues: dict[int, list[int]] = defaultdict(list)
    pr_to_prs: dict[int, list[int]] = defaultdict(list)
    commit_to_targets: dict[str, list[tuple]] = defaultdict(list)  # sha → [(type, id)]
    issue_to_commits: dict[int, list[str]] = defaultdict(list)

    for src_type, src_id, tgt_type, tgt_id, rel in edges:
        if src_type == "pr" and tgt_type == "issue":
            pr_to_issues[src_id].append(tgt_id)
            issue_to_prs[tgt_id].append(src_id)
        elif src_type == "pr" and tgt_type == "pr":
            pr_to_prs[src_id].append(tgt_id)
        elif src_type == "commit" and tgt_type == "issue":
            commit_to_targets[src_id].append(("issue", tgt_id))
            issue_to_commits[tgt_id].append(src_id)
        elif src_type == "commit" and tgt_type == "pr":
            commit_to_targets[src_id].append(("pr", tgt_id))
        elif src_type == "issue" and tgt_type == "pr":
            issue_to_prs[tgt_id]  # ensure key exists for completeness
            # Don't double-count; this is a weaker signal

    # Deduplicate within maps
    for d in [issue_to_prs, pr_to_issues, pr_to_prs, issue_to_commits]:
        for k in d:
            d[k] = sorted(set(d[k]))
    for k in commit_to_targets:
        commit_to_targets[k] = sorted(set(commit_to_targets[k]))

    # Stats
    total_edges = len(edges)
    n_issue_pr = len([e for e in edges
                      if (e[0] == "pr" and e[2] == "issue") or
                         (e[0] == "issue" and e[2] == "pr")])
    n_pr_pr = len([e for e in edges if e[0] == "pr" and e[2] == "pr"])
    n_commit_ref = len([e for e in edges if e[0] == "commit"])

    stats = {
        "total_edges": total_edges,
        "issue_pr_links": n_issue_pr,
        "pr_pr_links": n_pr_pr,
        "commit_refs": n_commit_ref,
    }

    log.info("[交叉引用] %d 条引用: Issue↔PR %d, PR↔PR %d, Commit→* %d",
             total_edges, n_issue_pr, n_pr_pr, n_commit_ref)

    return {
        "edges": edges,
        "issue_to_prs": dict(issue_to_prs),
        "pr_to_issues": dict(pr_to_issues),
        "pr_to_prs": dict(pr_to_prs),
        "commit_to_targets": dict(commit_to_targets),
        "issue_to_commits": dict(issue_to_commits),
        "stats": stats,
    }


def _truncate(text: str, max_len: int = 50) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").replace("\r", "").strip()
    return text[:max_len] + "..." if len(text) > max_len else text


def _state_icon(state: str) -> str:
    if state == "open":
        return "🟢"
    elif state == "merged":
        return "✅"
    elif state == "closed":
        return "🔴"
    return "⚪"


def _render_graph_png(
    xref: dict,
    issue_results: dict | None,
    pr_results: dict | None,
    commit_results: dict | None,
    output_path: str,
    max_nodes: int = 35,
) -> bool:
    """Render a cross-reference graph as a clean PNG image.

    Shows all reference types: Issue↔PR, PR↔PR, Commit→PR/Issue.
    Design: white bg, rounded-rect nodes, GitHub-inspired palette.
    Limits to top `max_nodes` nodes by degree to keep readable.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
        from matplotlib.lines import Line2D
        import networkx as nx
    except ImportError:
        log.warning("[交叉引用] matplotlib/networkx 不可用，跳过图片生成")
        return False

    try:  # Wrap entire rendering in try/except for robustness

        # CJK font
        import matplotlib.font_manager as fm
        for name in ["Microsoft YaHei", "SimHei", "WenQuanYi Micro Hei",
                      "Noto Sans CJK SC", "PingFang SC"]:
            if any(name in f.name for f in fm.fontManager.ttflist):
                plt.rcParams["font.sans-serif"] = [name] + plt.rcParams["font.sans-serif"]
                plt.rcParams["axes.unicode_minus"] = False
                break

        PAL = {
            # Nodes — one color per TYPE (not per role)
            "issue_fill": "#FDECEF", "issue_border": "#CF222E", "issue_text": "#8B1722",
            "pr_fill": "#DAFBE1", "pr_border": "#1A7F37", "pr_text": "#116329",
            "commit_fill": "#DDF4FF", "commit_border": "#0969DA", "commit_text": "#0550AE",
            # Edges — color by RELATIONSHIP (fixes vs refs)
            "edge_fix": "#CF222E",   # red   — thick solid
            "edge_ref": "#8B949E",   # gray  — thin dashed
            "bg": "#FFFFFF", "title": "#1F2328",
        }

        G = nx.DiGraph()
        node_meta = {}

        # Collect all nodes that appear in edges
        node_ids = set()
        for src_type, src_id, tgt_type, tgt_id, rel in xref["edges"]:
            if src_type == "issue":
                node_ids.add(("issue", src_id))
            elif src_type == "pr":
                node_ids.add(("pr", src_id))
            elif src_type == "commit":
                node_ids.add(("commit", src_id))
            if tgt_type == "issue":
                node_ids.add(("issue", tgt_id))
            elif tgt_type == "pr":
                node_ids.add(("pr", tgt_id))

        # Add nodes — compact labels (ID only, no title)
        for ntype, nid in node_ids:
            if ntype == "issue":
                gid = f"I#{nid}"
                label = f"#{nid}"
                G.add_node(gid)
                node_meta[gid] = dict(type="issue", fill=PAL["issue_fill"],
                                      border=PAL["issue_border"], text_color=PAL["issue_text"],
                                      label=label)
            elif ntype == "pr":
                gid = f"P#{nid}"
                label = f"#{nid}"
                G.add_node(gid)
                node_meta[gid] = dict(type="pr", fill=PAL["pr_fill"],
                                      border=PAL["pr_border"], text_color=PAL["pr_text"],
                                      label=label)
            elif ntype == "commit":
                gid = f"C:{str(nid)[:7]}"
                label = f"{str(nid)[:7]}"
                G.add_node(gid)
                node_meta[gid] = dict(type="commit", fill=PAL["commit_fill"],
                                      border=PAL["commit_border"], text_color=PAL["commit_text"],
                                      label=label)

        # Add edges — color/style purely by relationship type
        for src_type, src_id, tgt_type, tgt_id, rel in xref["edges"]:
            src_gid = {"issue": f"I#{src_id}", "pr": f"P#{src_id}",
                       "commit": f"C:{str(src_id)[:7]}"}[src_type]
            tgt_gid = {"issue": f"I#{tgt_id}", "pr": f"P#{tgt_id}"}[tgt_type]
            if src_gid in G.nodes and tgt_gid in G.nodes:
                G.add_edge(src_gid, tgt_gid, label=rel)

        if len(G.nodes) == 0:
            return False

        # Filter: only keep "hub" nodes with degree >= 2 (at least 2 connections)
        # This removes 1:1 simple links (e.g. one commit referencing one PR)
        # and keeps interesting multi-connection clusters
        hub_nodes = {n for n in G.nodes if G.degree(n) >= 2}
        if hub_nodes:
            # Also keep neighbors of hubs to show the cluster context
            hub_with_neighbors = set(hub_nodes)
            for h in hub_nodes:
                hub_with_neighbors.update(G.predecessors(h))
                hub_with_neighbors.update(G.successors(h))
            G = G.subgraph(hub_with_neighbors).copy()
            log.info("[交叉引用] 过滤后保留 %d 个枢纽+关联节点 (原始 %d)",
                     len(G.nodes), len(node_ids))
        else:
            # No hubs — fall back to top nodes by degree
            log.info("[交叉引用] 无枢纽节点，保留全部 %d 个节点", len(G.nodes))

        # Cap at max_nodes for readability
        if len(G.nodes) > max_nodes:
            top_nodes = sorted(G.nodes, key=lambda n: G.degree(n), reverse=True)[:max_nodes]
            G = G.subgraph(top_nodes).copy()

        if len(G.nodes) == 0:
            return False

        # Layout — smart column assignment
        # Split PRs into "source PRs" (that reference other PRs) and
        # "target PRs" (that are referenced by other PRs/commits).
        # This makes PR→PR arrows cross columns and become visible.
        issues = sorted([nd for nd in G.nodes if nd.startswith("I#")])
        all_prs = sorted([nd for nd in G.nodes if nd.startswith("P#")])
        commits = sorted([nd for nd in G.nodes if nd.startswith("C:")])

        # Find PRs that are targets of PR→PR edges
        pr_targets = set()
        for u, v, d in G.edges(data=True):
            if u.startswith("P#") and v.startswith("P#"):
                pr_targets.add(v)

        # Also find PRs that are targets of Commit→PR edges
        for u, v, d in G.edges(data=True):
            if u.startswith("C:") and v.startswith("P#"):
                pr_targets.add(v)

        # Split: "target PRs" go to a separate column for clean cross-column edges.
        # All PRs keep the SAME color — the arrow direction tells the story.
        if pr_targets:
            prs_left = sorted([p for p in all_prs if p not in pr_targets])
            prs_mid = sorted([p for p in all_prs if p in pr_targets])
        else:
            prs_left = all_prs
            prs_mid = []

        # Determine column positions based on what's present
        # Always: Issues(0) → Source PRs(1) → Target PRs(2) → Commits(3)
        col = 0.0
        col_map = {}

        COL_GAP = 1.5  # horizontal distance between columns
        if issues:
            col_map["issues"] = col
            col += COL_GAP
        if prs_left:
            col_map["prs_left"] = col
            col += COL_GAP
        if prs_mid:
            col_map["prs_mid"] = col
            col += COL_GAP
        if commits:
            col_map["commits"] = col

        # Y-spacing: each node gets 0.4 units of vertical space
        NODE_Y_STEP = 0.4

        def _spread_y(idx, count):
            """Spread items evenly on y-axis with enough spacing."""
            total_height = (count - 1) * NODE_Y_STEP
            top = total_height / 2.0
            return top - idx * NODE_Y_STEP

        pos = {}
        for i, nid in enumerate(issues):
            pos[nid] = (col_map.get("issues", 0), _spread_y(i, len(issues)))
        for i, nid in enumerate(prs_left):
            pos[nid] = (col_map.get("prs_left", 0), _spread_y(i, len(prs_left)))
        for i, nid in enumerate(prs_mid):
            pos[nid] = (col_map.get("prs_mid", COL_GAP * 2), _spread_y(i, len(prs_mid)))
        for i, nid in enumerate(commits):
            pos[nid] = (col_map.get("commits", COL_GAP * 3), _spread_y(i, len(commits)))

        n = len(G.nodes)
        n_cols = len([v for v in col_map.values()])
        max_col_len = max(len(prs_left), len(prs_mid), len(commits), len(issues), 1)
        fig_w = max(8, n_cols * 3.5)
        fig_h = max(3, max_col_len * 0.55)
        fig, ax = plt.subplots(1, 1, figsize=(fig_w, fig_h))
        fig.patch.set_facecolor(PAL["bg"])
        ax.set_facecolor(PAL["bg"])

        NODE_W = 0.38  # half-width
        NODE_H = 0.12  # half-height

        # ── Draw edges ──────────────────────────────────────────────
        # Design: ALL edges connect at the CENTER of the short side.
        #   Cross-column: right-center of source → left-center of target
        #   Same-column:  right-center of both, outward arc to the right
        # Style: entirely determined by relationship type:
        #   fixes = thick red solid;  refs = thin gray dashed
        edge_list = list(G.edges(data=True))
        has_same_col = False

        for _eidx, (u, v, d) in enumerate(edge_list):
            rel = d.get("label", "refs")
            if rel == "fixes":
                e_color, e_width, e_alpha = PAL["edge_fix"], 2.5, 0.9
                linestyle, arrow_scale = "-", 16
            else:
                e_color, e_width, e_alpha = PAL["edge_ref"], 1.0, 0.5
                linestyle, arrow_scale = "--", 10

            ux, uy = pos[u]
            vx, vy = pos[v]

            if abs(ux - vx) < 0.01:
                # Same-column: right-center → right-center, outward arc
                has_same_col = True
                sx, sy = ux + NODE_W, uy
                ex, ey = vx + NODE_W, vy
                dy = abs(sy - ey)
                arc_mag = max(0.55, dy * 0.5)
                # Positive rad bows LEFT of travel; for downward = right ✓
                arc_rad = arc_mag if sy > ey else -arc_mag
                conn = f"arc3,rad={arc_rad:.3f}"
            elif ux < vx:
                # Cross-column L→R: right-center → left-center
                sx, sy = ux + NODE_W, uy
                ex, ey = vx - NODE_W, vy
                conn = "arc3,rad=0"
            else:
                # Cross-column R→L: left-center → right-center
                sx, sy = ux - NODE_W, uy
                ex, ey = vx + NODE_W, vy
                conn = "arc3,rad=0"

            arrow = FancyArrowPatch(
                (sx, sy), (ex, ey),
                arrowstyle="-|>", mutation_scale=arrow_scale,
                color=e_color, linewidth=e_width, alpha=e_alpha,
                linestyle=linestyle, connectionstyle=conn, zorder=1,
            )
            ax.add_patch(arrow)

        # Draw nodes — compact rounded pills with ID only
        for nid in G.nodes:
            if nid not in pos:
                continue
            x, y = pos[nid]
            meta = node_meta.get(nid, {})
            if not meta:
                continue
            # Shadow
            shadow = FancyBboxPatch(
                (x - NODE_W + 0.01, y - NODE_H + 0.01),
                NODE_W * 2, NODE_H * 2,
                boxstyle="round,pad=0.03",
                facecolor="#D0D7DE", edgecolor="none", alpha=0.18,
                transform=ax.transData, zorder=1,
            )
            ax.add_patch(shadow)
            # Main box
            box = FancyBboxPatch(
                (x - NODE_W, y - NODE_H),
                NODE_W * 2, NODE_H * 2,
                boxstyle="round,pad=0.03",
                facecolor=meta["fill"], edgecolor=meta["border"],
                linewidth=1.4, transform=ax.transData, zorder=2,
            )
            ax.add_patch(box)
            # Single-line label (just the ID)
            ax.text(x, y, meta["label"],
                    fontsize=8, fontweight="bold", color=meta["text_color"],
                    ha="center", va="center", transform=ax.transData, zorder=3)

        # Legend — node types + edge styles in one block
        legend_items = []
        types_seen = {node_meta[nd]["type"] for nd in G.nodes if nd in node_meta}
        if "issue" in types_seen:
            legend_items.append(mpatches.Patch(facecolor=PAL["issue_fill"],
                                edgecolor=PAL["issue_border"], linewidth=1.5, label="Issue"))
        if "pr" in types_seen:
            legend_items.append(mpatches.Patch(facecolor=PAL["pr_fill"],
                                edgecolor=PAL["pr_border"], linewidth=1.5, label="PR"))
        if "commit" in types_seen:
            legend_items.append(mpatches.Patch(facecolor=PAL["commit_fill"],
                                edgecolor=PAL["commit_border"], linewidth=1.5, label="Commit"))
        # Edge style entries
        rels_seen = {d.get("label", "refs") for _, _, d in G.edges(data=True)}
        if "fixes" in rels_seen:
            legend_items.append(Line2D([0], [0], color=PAL["edge_fix"],
                                linewidth=2.5, linestyle="-", label="fixes / closes"))
        if "refs" in rels_seen:
            legend_items.append(Line2D([0], [0], color=PAL["edge_ref"],
                                linewidth=1.0, linestyle="--", label="refs (引用)"))
        if legend_items:
            leg = ax.legend(handles=legend_items, loc="upper right", fontsize=8,
                            frameon=True, fancybox=True, facecolor="white",
                            edgecolor="#D0D7DE", framealpha=0.95)
            leg.get_frame().set_linewidth(0.8)

        ax.set_title("Cross-Reference Graph", fontsize=14, color=PAL["title"],
                     fontweight="bold", pad=15, loc="left")

        all_x = [p[0] for p in pos.values()]
        all_y = [p[1] for p in pos.values()]
        r_margin = 1.6 if has_same_col else 0.6
        ax.set_xlim(min(all_x) - 0.6, max(all_x) + r_margin)
        ax.set_ylim(min(all_y) - 0.3, max(all_y) + 0.35)
        ax.axis("off")

        plt.tight_layout(pad=1.2)
        plt.savefig(output_path, dpi=180, bbox_inches="tight",
                    facecolor=PAL["bg"], edgecolor="none")
        plt.close(fig)

        log.info("[交叉引用] 关系图已保存: %s", output_path)
        return True

    except Exception as e:
        log.warning("[交叉引用] 图片生成失败: %s", e)
        return False


def format_cross_ref_summary(
    xref: dict,
    issue_results: dict | None = None,
    pr_results: dict | None = None,
    commit_results: dict | None = None,
    repo: str = "",
    output_dir: str = "",
) -> str:
    """Generate a rich Markdown cross-reference section.

    Includes all reference types: Issue↔PR, PR↔PR, Commit→*.
    """
    stats = xref["stats"]
    if stats["total_edges"] == 0:
        return ""

    repo_url = f"https://github.com/{repo}" if repo else ""
    lines = [
        "## 交叉引用",
        "",
        f"> 共发现 **{stats['total_edges']}** 条引用关系: "
        f"Issue↔PR **{stats['issue_pr_links']}**, "
        f"PR↔PR **{stats['pr_pr_links']}**, "
        f"Commit引用 **{stats['commit_refs']}**",
        "",
    ]

    def _link(typ: str, num) -> str:
        if typ == "issue":
            return f"[#{num}]({repo_url}/issues/{num})" if repo_url else f"#{num}"
        elif typ == "pr":
            return f"[#{num}]({repo_url}/pull/{num})" if repo_url else f"#{num}"
        elif typ == "commit":
            return f"[`{str(num)[:8]}`]({repo_url}/commit/{num})" if repo_url else f"`{str(num)[:8]}`"
        return str(num)

    def _title_of(typ: str, num):
        if typ == "issue" and issue_results and num in issue_results:
            return _truncate(issue_results[num].title, 45)
        if typ == "pr" and pr_results and num in pr_results:
            return _truncate(pr_results[num].title, 45)
        if typ == "commit" and commit_results:
            for c in commit_results.values():
                if c.sha[:10] == str(num)[:10]:
                    return _truncate(c.message, 45)
        return ""

    def _state_of(typ: str, num) -> str:
        if typ == "issue" and issue_results and num in issue_results:
            return _state_icon(issue_results[num].state)
        if typ == "pr" and pr_results and num in pr_results:
            obj = pr_results[num]
            if getattr(obj, "merged", False):
                return "✅"
            return _state_icon(obj.state)
        return ""

    # --- Section 1: Issue ↔ PR ---
    if xref["issue_to_prs"]:
        lines.append("### Issue ↔ PR 关联")
        lines.append("")
        lines.append("| Issue | 标题 | 状态 | 关联 PR | PR 标题 | PR 状态 |")
        lines.append("|-------|------|------|---------|---------|---------|")
        for inum in sorted(xref["issue_to_prs"].keys()):
            pr_list = xref["issue_to_prs"][inum]
            for j, pn in enumerate(pr_list):
                if j == 0:
                    lines.append(f"| {_link('issue', inum)} | {_title_of('issue', inum)} "
                                 f"| {_state_of('issue', inum)} "
                                 f"| {_link('pr', pn)} | {_title_of('pr', pn)} "
                                 f"| {_state_of('pr', pn)} |")
                else:
                    lines.append(f"| ↳ | | | {_link('pr', pn)} | {_title_of('pr', pn)} "
                                 f"| {_state_of('pr', pn)} |")
        lines.append("")

    # --- Section 2: PR → PR ---
    if xref["pr_to_prs"]:
        lines.append("### PR → PR 关联")
        lines.append("")
        lines.append("| 来源 PR | 标题 | 状态 | 引用 PR | 被引用 PR 标题 | 状态 |")
        lines.append("|---------|------|------|---------|--------------|------|")
        for src_pn in sorted(xref["pr_to_prs"].keys()):
            tgt_list = xref["pr_to_prs"][src_pn]
            for j, tgt_pn in enumerate(tgt_list):
                if j == 0:
                    lines.append(f"| {_link('pr', src_pn)} | {_title_of('pr', src_pn)} "
                                 f"| {_state_of('pr', src_pn)} "
                                 f"| {_link('pr', tgt_pn)} | {_title_of('pr', tgt_pn)} "
                                 f"| {_state_of('pr', tgt_pn)} |")
                else:
                    lines.append(f"| ↳ | | | {_link('pr', tgt_pn)} | {_title_of('pr', tgt_pn)} "
                                 f"| {_state_of('pr', tgt_pn)} |")
        lines.append("")

    # --- Section 3: Commit → Issue/PR ---
    if xref["commit_to_targets"]:
        lines.append("### Commit 引用")
        lines.append("")
        lines.append("| Commit | 提交信息 | 引用目标 | 目标标题 | 状态 |")
        lines.append("|--------|---------|---------|---------|------|")
        for sha in sorted(xref["commit_to_targets"].keys()):
            targets = xref["commit_to_targets"][sha]
            for j, (tgt_type, tgt_id) in enumerate(targets):
                if j == 0:
                    lines.append(f"| {_link('commit', sha)} | {_title_of('commit', sha)} "
                                 f"| {_link(tgt_type, tgt_id)} | {_title_of(tgt_type, tgt_id)} "
                                 f"| {_state_of(tgt_type, tgt_id)} |")
                else:
                    lines.append(f"| ↳ | | {_link(tgt_type, tgt_id)} "
                                 f"| {_title_of(tgt_type, tgt_id)} "
                                 f"| {_state_of(tgt_type, tgt_id)} |")
        lines.append("")

    # --- PNG graph ---
    img_dir = output_dir or os.getcwd()
    img_name = "cross_ref_graph.png"
    img_path = os.path.join(img_dir, img_name)

    rendered = _render_graph_png(
        xref, issue_results, pr_results, commit_results, img_path)

    if rendered:
        lines.append("### 关系图")
        lines.append("")
        lines.append(f"![交叉引用关系图]({img_name})")
        lines.append("")

    lines.extend(["---", ""])
    return "\n".join(lines)
