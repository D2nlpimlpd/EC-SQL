#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Generate xiezuo.png: EC-SQL multi-agent pipeline diagram
with Final Repair step between Repairer and Oracle DB.
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.patches import Arc
import numpy as np

fig, ax = plt.subplots(figsize=(14, 6))
ax.set_xlim(0, 14)
ax.set_ylim(-4, 3)
ax.axis('off')

# ── colour palette ──────────────────────────────────────────────────────────
C_AGENT  = '#D6E8FA'
C_DB     = '#FFE0B2'
C_FINAL  = '#FFD6D6'
C_KB     = '#E8F5E9'
C_EDGE   = '#2C3E50'
C_RED    = '#C0392B'
C_GRAY   = '#7F8C8D'
C_GREEN  = '#27AE60'

# ── helper: rounded rectangle ───────────────────────────────────────────────
def box(ax, cx, cy, w, h, color, edgecolor=C_EDGE, lw=1.5, ls='-', radius=0.18):
    b = FancyBboxPatch((cx - w/2, cy - h/2), w, h,
                       boxstyle=f'round,pad={radius}',
                       facecolor=color, edgecolor=edgecolor,
                       linewidth=lw, linestyle=ls)
    ax.add_patch(b)
    return (cx - w/2, cy - h/2, w, h)

def label(ax, cx, cy, text, fs=10, color='black', bold=False):
    weight = 'bold' if bold else 'normal'
    ax.text(cx, cy, text, ha='center', va='center',
            fontsize=fs, color=color, fontweight=weight)

def arrow(ax, x1, y1, x2, y2, color=C_EDGE, lw=1.5, ls='-',
          arrowstyle='->', mutation_scale=14):
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle=arrowstyle, color=color,
                                lw=lw, linestyle=ls))

def edge_label(ax, x, y, text, fs=7.5, color=C_GRAY):
    ax.text(x, y, text, ha='center', va='center',
            fontsize=fs, color=color,
            bbox=dict(facecolor='white', edgecolor='none', pad=1))

# ── agent positions (y=1) ───────────────────────────────────────────────────
agents = [
    (1.2,  1.0, 'Retriever'),
    (3.2,  1.0, 'Planner'),
    (5.5,  1.0, 'Generator'),
    (7.8,  1.0, 'Guard'),
    (10.1, 1.0, 'Repairer'),
]
AW, AH = 1.6, 0.55

for cx, cy, name in agents:
    box(ax, cx, cy, AW, AH, C_AGENT)
    label(ax, cx, cy, name, fs=9, bold=True)

# ── arrows between agents ────────────────────────────────────────────────────
edge_labels = ['Schema', 'Plan', 'SQL', 'Errors']
for i in range(len(agents)-1):
    x1 = agents[i][0]   + AW/2
    x2 = agents[i+1][0] - AW/2
    y  = agents[i][1]
    arrow(ax, x1, y, x2, y)
    edge_label(ax, (x1+x2)/2, y+0.22, edge_labels[i])

# ── NL Query input ───────────────────────────────────────────────────────────
arrow(ax, 0.05, 1.0, agents[0][0]-AW/2, 1.0)
label(ax, -0.35, 1.0, 'NL\nQuery', fs=8, color=C_EDGE)

# ── SQL output ───────────────────────────────────────────────────────────────
arrow(ax, agents[-1][0]+AW/2, 1.0, 13.3, 1.0)
label(ax, 13.6, 1.0, 'SQL', fs=9, bold=True, color=C_EDGE)

# ── Repair Hints back-arc (Repairer → Generator) ─────────────────────────────
rep_cx = agents[4][0]
gen_cx = agents[2][0]
arc_y  = 2.15
# draw polyline: rep_top -> up -> left -> gen_top
ax.annotate('', xy=(gen_cx, agents[2][1]+AH/2),
            xytext=(rep_cx, agents[4][1]+AH/2),
            arrowprops=dict(
                arrowstyle='->',
                color=C_EDGE, lw=1.5,
                connectionstyle='arc,angleA=90,angleB=90,armA=40,armB=40,rad=8'
            ))
edge_label(ax, (rep_cx+gen_cx)/2, arc_y, 'Repair Hints', fs=8, color=C_EDGE)

# ── Oracle DB cylinder ───────────────────────────────────────────────────────
db_cx, db_cy = 10.1, -1.6
db_w,  db_h  = 1.6,  0.7
ell_ry = 0.18
# body
db_rect = plt.Rectangle((db_cx-db_w/2, db_cy-db_h/2),
                         db_w, db_h, facecolor=C_DB,
                         edgecolor=C_EDGE, lw=1.5, zorder=2)
ax.add_patch(db_rect)
# top ellipse
top_ell = mpatches.Ellipse((db_cx, db_cy+db_h/2), db_w, ell_ry*2,
                            facecolor=C_DB, edgecolor=C_EDGE, lw=1.5, zorder=3)
ax.add_patch(top_ell)
# bottom ellipse
bot_ell = mpatches.Ellipse((db_cx, db_cy-db_h/2), db_w, ell_ry*2,
                            facecolor=C_DB, edgecolor=C_EDGE, lw=1.5, zorder=3)
ax.add_patch(bot_ell)
label(ax, db_cx, db_cy, 'Oracle DB', fs=8.5, bold=True)

# ── Repairer <-> Oracle DB ────────────────────────────────────────────────────
# down: Repairer -> DB  (catalog query)
arrow(ax, rep_cx+0.15, agents[4][1]-AH/2,
         db_cx+0.15,  db_cy+db_h/2+ell_ry, color=C_EDGE)
edge_label(ax, rep_cx+0.9, (agents[4][1]-AH/2 + db_cy+db_h/2+ell_ry)/2,
           'Catalog\nQuery', fs=7.5)
# up: DB -> Repairer  (catalog result)
arrow(ax, db_cx-0.15, db_cy+db_h/2+ell_ry,
         rep_cx-0.15, agents[4][1]-AH/2, color=C_EDGE)
edge_label(ax, rep_cx-0.85, (agents[4][1]-AH/2 + db_cy+db_h/2+ell_ry)/2,
           'Catalog\nResult', fs=7.5)

# ── Guard -> Oracle DB  (execute) ────────────────────────────────────────────
grd_cx = agents[3][0]
arrow(ax, grd_cx, agents[3][1]-AH/2,
         grd_cx, db_cy+db_h/2+ell_ry-0.05,
         color=C_GRAY, lw=1.2)
edge_label(ax, grd_cx-0.55, (agents[3][1]-AH/2 + db_cy+db_h/2+ell_ry)/2,
           'Execute', fs=7.5, color=C_GRAY)

# ── Final Repair box ─────────────────────────────────────────────────────────
fr_cx, fr_cy = 6.8, -1.6
fr_w,  fr_h  = 1.8,  0.55
box(ax, fr_cx, fr_cy, fr_w, fr_h, C_FINAL,
    edgecolor=C_RED, lw=1.5, ls='--')
label(ax, fr_cx, fr_cy, 'Final Repair', fs=9, bold=True, color=C_RED)

# dashed: Repairer -> Final Repair
arrow(ax, rep_cx-AW/2, agents[4][1]-AH/2,
         fr_cx+fr_w/2, fr_cy,
         color=C_RED, lw=1.3, ls='dashed')
edge_label(ax, (rep_cx-AW/2+fr_cx+fr_w/2)/2 + 0.1,
           (agents[4][1]-AH/2 + fr_cy)/2 - 0.1,
           'if all rounds fail', fs=7, color=C_RED)

# dashed: Final Repair -> Generator (full schema + error history)
arrow(ax, fr_cx, fr_cy+fr_h/2,
         gen_cx, agents[2][1]-AH/2,
         color=C_RED, lw=1.3, ls='dashed')
edge_label(ax, (fr_cx+gen_cx)/2 - 0.3,
           (fr_cy+fr_h/2 + agents[2][1]-AH/2)/2,
           'Full Schema +\nError History', fs=7, color=C_RED)

# ── Shared Knowledge Base ─────────────────────────────────────────────────────
kb_cx, kb_cy = 5.5, -3.1
kb_w,  kb_h  = 9.0,  0.55
box(ax, kb_cx, kb_cy, kb_w, kb_h, C_KB,
    edgecolor=C_GREEN, lw=1.5)
label(ax, kb_cx, kb_cy,
      'Shared Knowledge Base  (Schema Index + Data Dictionary)',
      fs=8.5, color=C_GREEN)

# arrows from each agent down to KB
for cx, cy, _ in agents:
    ax.annotate('', xy=(cx, kb_cy+kb_h/2),
                xytext=(cx, cy-AH/2),
                arrowprops=dict(arrowstyle='->', color='#AAAAAA',
                                lw=1.0, linestyle='dotted'))

plt.tight_layout()
plt.savefig(r'c:\Users\wangh\Desktop\text2sql\xiezuo.png',
            dpi=180, bbox_inches='tight', facecolor='white')
print('DONE: xiezuo.png saved')


