"""Generate the demo document set for the fictional company "Lumora Systems".

Produces, in sample_docs/:
  - Lumora_Employee_Handbook_2027.pdf  (text + holiday calendar image + leave flow diagram + org chart)
  - Lumora_Platform_Architecture.docx  (text + architecture diagram + incident flow image + table)
  - lumora_it_helpdesk_faq.html        (a web page)
  - images/*.png                       (the images embedded above, kept for reference)

Some facts live ONLY inside the images (holiday dates, approval rules, service names),
so the demo proves the bot can answer questions from pictures, not just from text.

Run:  python scripts/make_sample_docs.py
"""

from __future__ import annotations

import calendar
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "sample_docs"
IMG = OUT / "images"

BLUE = "#2563eb"
LIGHT = "#dbeafe"
GREY = "#475569"
RED = "#dc2626"
GREEN = "#16a34a"

HOLIDAYS_2027 = {
    (1, 1): "New Year's Day",
    (1, 26): "Republic Day",
    (4, 16): "Founders' Day",
    (7, 30): "Wellness Day",
    (8, 15): "Independence Day",
    (10, 2): "Gandhi Jayanti",
    (12, 24): "Year-end shutdown",
    (12, 25): "Christmas Day",
    (12, 31): "Year-end shutdown",
}


# --------------------------------------------------------------------------- images


def holiday_calendar(path: Path) -> None:
    fig = plt.figure(figsize=(12, 10.5), dpi=110)
    fig.suptitle("Lumora Systems - 2027 Company Holiday Calendar", fontsize=18, fontweight="bold", y=0.985)
    cal = calendar.Calendar(firstweekday=0)
    for m in range(1, 13):
        ax = fig.add_axes([0.03 + ((m - 1) % 4) * 0.245, 0.70 - ((m - 1) // 4) * 0.25, 0.22, 0.21])
        ax.set_xlim(0, 7)
        ax.set_ylim(0, 7.4)
        ax.axis("off")
        ax.text(3.5, 7.0, calendar.month_name[m], ha="center", fontsize=12, fontweight="bold", color=BLUE)
        for i, d in enumerate("MTWTFSS"):
            ax.text(i + 0.5, 6.2, d, ha="center", fontsize=8, color=GREY, fontweight="bold")
        for w, week in enumerate(cal.monthdayscalendar(2027, m)):
            for i, day in enumerate(week):
                if not day:
                    continue
                y = 5.3 - w
                if (m, day) in HOLIDAYS_2027:
                    ax.add_patch(plt.Circle((i + 0.5, y + 0.12), 0.42, color=RED))
                    ax.text(i + 0.5, y, str(day), ha="center", fontsize=8, color="white", fontweight="bold")
                else:
                    ax.text(i + 0.5, y, str(day), ha="center", fontsize=8, color="#94a3b8" if i >= 5 else "black")
    legend = fig.add_axes([0.03, 0.0, 0.94, 0.17])
    legend.axis("off")
    legend.text(0, 0.92, "Paid company holidays (red):", fontsize=12, fontweight="bold")
    items = [f"{calendar.month_abbr[m]} {d}: {name}" for (m, d), name in sorted(HOLIDAYS_2027.items())]
    for idx, item in enumerate(items):
        legend.text((idx % 3) * 0.34, 0.68 - (idx // 3) * 0.24, item, fontsize=11)
    fig.savefig(path)
    plt.close(fig)


def _box(ax, x, y, w, h, text, fill="white", edge=GREY, bold=False, size=10):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                fc=fill, ec=edge, lw=1.5))
    ax.text(x, y, text, ha="center", va="center", fontsize=size, fontweight="bold" if bold else "normal", wrap=True)


def _arrow(ax, a, b, label=None, color=GREY, label_offset=(0, 0.12)):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=14, color=color, lw=1.4))
    if label:
        ax.text((a[0] + b[0]) / 2 + label_offset[0], (a[1] + b[1]) / 2 + label_offset[1], label,
                ha="center", fontsize=9, color=color, style="italic")


def leave_flow(path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 5.2), dpi=110)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 5.2)
    ax.axis("off")
    ax.set_title("Leave approval workflow", fontsize=15, fontweight="bold", loc="left")
    _box(ax, 1.2, 3.6, 2.0, 0.9, "Employee submits\nrequest in\nPeopleHub")
    _box(ax, 4.0, 3.6, 2.2, 0.9, "Line manager\nreviews within\n2 working days")
    ax.add_patch(plt.Polygon([[6.9, 4.3], [7.9, 3.6], [6.9, 2.9], [5.9, 3.6]], fc="white", ec=GREY, lw=1.5))
    ax.text(6.9, 3.6, "More than\n5 days?", ha="center", va="center", fontsize=9)
    _box(ax, 10.2, 3.6, 2.4, 0.9, "HR Business Partner\nreview within\n3 working days")
    _box(ax, 6.9, 1.0, 2.4, 0.9, "Approved: synced to\nteam calendar +\npayroll", fill=LIGHT, edge=BLUE, bold=True)
    _box(ax, 1.2, 1.0, 2.0, 0.9, "Rejected: employee\nnotified with reason", edge=RED)
    _arrow(ax, (2.2, 3.6), (2.9, 3.6))
    _arrow(ax, (5.1, 3.6), (5.9, 3.6), "approve")
    _arrow(ax, (7.9, 3.6), (9.0, 3.6), "yes")
    _arrow(ax, (6.9, 2.9), (6.9, 1.45), "no", label_offset=(0.25, 0))
    _arrow(ax, (10.2, 3.15), (8.1, 1.2), "approve", label_offset=(0.5, 0.1))
    _arrow(ax, (4.0, 3.15), (1.6, 1.45), "reject", color=RED, label_offset=(-0.3, 0.1))
    ax.text(0.2, 0.1, "Sick leave of 3+ consecutive days needs a medical certificate uploaded within 48 hours.",
            fontsize=10, color=GREY)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def org_chart(path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 4.6), dpi=110)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 4.6)
    ax.axis("off")
    ax.set_title("Lumora Systems leadership (as of January 2027)", fontsize=15, fontweight="bold", loc="left")
    _box(ax, 6, 3.6, 3.0, 0.8, "Priya Raman\nChief Executive Officer", fill=LIGHT, edge=BLUE, bold=True)
    people = [
        (1.5, "Daniel Okafor\nChief Technology Officer"),
        (4.5, "Meera Iyer\nVP People & Culture"),
        (7.5, "Lucas Fernandes\nChief Financial Officer"),
        (10.5, "Hana Sato\nVP Customer Success"),
    ]
    for x, label in people:
        _box(ax, x, 1.9, 2.6, 0.8, label)
        ax.plot([6, 6, x, x], [3.2, 2.75, 2.75, 2.3], color=GREY, lw=1.3)
    ax.text(1.5, 0.9, "Platform, Security,\nData teams", ha="center", fontsize=9, color=GREY)
    ax.text(4.5, 0.9, "HR Business Partners,\nTalent, L&D", ha="center", fontsize=9, color=GREY)
    ax.text(7.5, 0.9, "Finance, Payroll,\nProcurement", ha="center", fontsize=9, color=GREY)
    ax.text(10.5, 0.9, "Support desk,\nOnboarding", ha="center", fontsize=9, color=GREY)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def architecture(path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 6.6), dpi=110)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 6.6)
    ax.axis("off")
    ax.set_title("OrbitDesk platform architecture (production, region: Central India)", fontsize=14,
                 fontweight="bold", loc="left")
    _box(ax, 1.3, 5.4, 2.0, 0.7, "Web app\n(React)")
    _box(ax, 1.3, 4.3, 2.0, 0.7, "Mobile app\n(Flutter)")
    _box(ax, 4.0, 4.85, 2.0, 1.0, "API Gateway\n(Kong)\nrate limit 600 rpm", fill=LIGHT, edge=BLUE, bold=True)
    services = [(5.6, "Booking service\n(Python, FastAPI)"), (4.4, "Billing service\n(Go)"),
                (3.2, "Notification service\n(Node.js)")]
    for y, label in services:
        _box(ax, 7.0, y, 2.4, 0.8, label)
        _arrow(ax, (5.0, 4.85), (5.8, y))
    _box(ax, 10.5, 5.6, 2.2, 0.7, "PostgreSQL 16\n(primary + replica)")
    _box(ax, 10.5, 4.4, 2.2, 0.7, "Redis cache\n(TTL 15 min)")
    _box(ax, 10.5, 3.2, 2.2, 0.7, "Kafka\n(event bus)")
    _arrow(ax, (8.2, 5.6), (9.4, 5.6))
    _arrow(ax, (8.2, 4.4), (9.4, 4.4))
    _arrow(ax, (8.2, 3.2), (9.4, 3.2))
    _arrow(ax, (2.3, 5.4), (3.0, 5.0))
    _arrow(ax, (2.3, 4.3), (3.0, 4.7))
    ax.add_patch(FancyBboxPatch((0.3, 0.4), 11.4, 1.6, boxstyle="round,pad=0.02,rounding_size=0.1",
                                fc="#f1f5f9", ec=GREY, lw=1, ls="--"))
    ax.text(0.5, 1.75, "Observability and operations", fontsize=10, fontweight="bold", color=GREY)
    ax.text(0.5, 1.2, "Grafana dashboards  |  Prometheus metrics  |  Loki logs  |  PagerDuty on-call rotation",
            fontsize=10)
    ax.text(0.5, 0.7, "Deploys: GitHub Actions -> Kubernetes (AKS), blue/green, every weekday 11:00 IST",
            fontsize=10)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def incident_severity(path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 3.4), dpi=110)
    ax.axis("off")
    ax.set_title("Incident severity matrix", fontsize=14, fontweight="bold", loc="left")
    rows = [["SEV1", "Platform down or data loss", "15 min", "CTO + on-call lead"],
            ["SEV2", "Major feature degraded", "30 min", "On-call lead"],
            ["SEV3", "Minor bug, workaround exists", "1 business day", "Owning team"]]
    table = ax.table(cellText=rows, colLabels=["Level", "Example", "Response time", "Escalate to"],
                     loc="center", cellLoc="left", colWidths=[0.1, 0.4, 0.2, 0.3])
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 2.0)
    for (r, c), cell in table.get_celld().items():
        if r == 0:
            cell.set_facecolor(LIGHT)
            cell.set_text_props(fontweight="bold")
        elif c == 0:
            cell.set_text_props(color=[RED, "#d97706", GREEN][r - 1], fontweight="bold")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------------- documents


def handbook_pdf(path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    body, h1, h2 = styles["BodyText"], styles["Title"], styles["Heading2"]
    width = A4[0] - 4 * cm

    def img(p: Path, ratio: float) -> Image:
        return Image(str(p), width=width, height=width * ratio)

    story = [
        Paragraph("Lumora Systems Employee Handbook 2027", h1),
        Paragraph("Version 3.1, effective 1 January 2027. Owner: People & Culture team.", body),
        Spacer(1, 12),
        Paragraph("1. Working hours", h2),
        Paragraph("Core collaboration hours are 10:30 to 16:30 IST, Monday to Friday. Outside core hours, "
                  "employees choose their own schedule as long as they work 40 hours a week. Lumora is hybrid: "
                  "teams meet in the Bengaluru office on Tuesdays and Thursdays.", body),
        Paragraph("2. Leave policy", h2),
        Paragraph("Every full-time employee receives the following leave each calendar year. Unused earned leave "
                  "carries over up to a maximum of 10 days; casual and sick leave do not carry over.", body),
        Spacer(1, 6),
    ]
    leave = Table([["Leave type", "Days per year", "Carry over"],
                   ["Earned leave", "18", "Up to 10 days"],
                   ["Casual leave", "8", "No"],
                   ["Sick leave", "10", "No"],
                   ["Parental leave", "26 weeks (birth parent), 6 weeks (other parent)", "n/a"]],
                  colWidths=[4 * cm, 8 * cm, 4 * cm])
    leave.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(LIGHT)),
                               ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                               ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]))
    story += [
        leave,
        Spacer(1, 10),
        Paragraph("How leave is requested and approved is shown in the workflow below.", body),
        Spacer(1, 6),
        img(IMG / "leave_approval_flow.png", 0.44),
        PageBreak(),
        Paragraph("3. Company holidays", h2),
        Paragraph("Lumora observes the paid company holidays marked on the calendar below. If a holiday falls on "
                  "a weekend, it is not moved to a weekday.", body),
        Spacer(1, 6),
        img(IMG / "holiday_calendar_2027.png", 0.86),
        PageBreak(),
        Paragraph("4. Who's who", h2),
        Paragraph("The leadership team and the functions each leader owns are shown in the org chart.", body),
        Spacer(1, 6),
        img(IMG / "org_chart.png", 0.40),
        Paragraph("5. Expenses and equipment", h2),
        Paragraph("Each employee gets a one-time home office allowance of INR 25,000 in their first 90 days, "
                  "claimed through the Expenses tab in PeopleHub with receipts. Laptops are refreshed every "
                  "3 years. Travel above INR 50,000 per trip needs CFO pre-approval.", body),
        Paragraph("6. Learning budget", h2),
        Paragraph("Each employee has an annual learning budget of INR 40,000 for courses, certifications and "
                  "conferences. Requests go to your line manager and are reimbursed after completion.", body),
    ]
    SimpleDocTemplate(str(path), pagesize=A4, title="Lumora Employee Handbook 2027",
                      leftMargin=2 * cm, rightMargin=2 * cm).build(story)


def architecture_docx(path: Path) -> None:
    from docx import Document
    from docx.shared import Cm

    doc = Document()
    doc.core_properties.title = "OrbitDesk Platform Architecture"
    doc.add_heading("OrbitDesk Platform Architecture", 0)
    doc.add_paragraph("Owner: Platform team (Daniel Okafor, CTO). Last reviewed: 12 January 2027.")
    doc.add_heading("Overview", 1)
    doc.add_paragraph(
        "OrbitDesk is Lumora's desk and meeting-room booking product, used by 140 enterprise customers. "
        "Clients talk only to the API gateway; each backend service owns its own data and publishes events "
        "for the others. The diagram shows the production layout.")
    doc.add_picture(str(IMG / "architecture.png"), width=Cm(16))
    doc.add_heading("Service ownership", 1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    for cell, text in zip(table.rows[0].cells, ["Service", "Team", "On-call Slack channel"]):
        cell.text = text
    for row in [("Booking service", "Team Atlas", "#oncall-atlas"),
                ("Billing service", "Team Ledger", "#oncall-ledger"),
                ("Notification service", "Team Echo", "#oncall-echo")]:
        cells = table.add_row().cells
        for cell, text in zip(cells, row):
            cell.text = text
    doc.add_heading("Availability targets", 1)
    doc.add_paragraph("The booking API targets 99.9% monthly availability with p95 latency under 300 ms. "
                      "Billing targets 99.95% because failed payments create support tickets. Database backups "
                      "run every 6 hours and are kept for 35 days.")
    doc.add_heading("Incident response", 1)
    doc.add_paragraph("Incidents are classified by severity. Response times and escalation paths are in the "
                      "matrix below. Every SEV1 and SEV2 incident needs a written post-mortem within 5 working days.")
    doc.add_picture(str(IMG / "incident_severity.png"), width=Cm(15))
    doc.save(str(path))


HELPDESK_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Lumora IT Helpdesk FAQ</title></head>
<body>
<h1>Lumora IT Helpdesk FAQ</h1>
<p>Last updated 8 January 2027. Raise anything not covered here at helpdesk.lumora.example or call extension 4357.</p>
<h2>How do I connect to the VPN?</h2>
<p>Install GlobalProtect from the Self Service app, enter the portal vpn.lumora.example and sign in with your
Lumora account and authenticator app. The VPN is required for PeopleHub and the internal wiki when you are
outside the office.</p>
<h2>How do I reset my password?</h2>
<p>Go to password.lumora.example, verify with your authenticator app and choose a new password of at least
14 characters. Passwords expire every 180 days. After 5 failed sign-in attempts your account locks for 15 minutes.</p>
<h2>What laptop will I get?</h2>
<p>Engineers receive a 16-inch laptop with 32 GB RAM; everyone else receives a 14-inch laptop with 16 GB RAM.
Laptops are refreshed every 3 years.</p>
<h2>Can I install my own software?</h2>
<p>Approved tools are available in the Self Service app. For anything else, open a Software Request ticket;
the security team reviews requests within 3 working days.</p>
<h2>Who do I contact about a lost device?</h2>
<p>Report a lost or stolen device to security@lumora.example within 1 hour so it can be remotely wiped.</p>
</body></html>
"""


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    holiday_calendar(IMG / "holiday_calendar_2027.png")
    leave_flow(IMG / "leave_approval_flow.png")
    org_chart(IMG / "org_chart.png")
    architecture(IMG / "architecture.png")
    incident_severity(IMG / "incident_severity.png")
    handbook_pdf(OUT / "Lumora_Employee_Handbook_2027.pdf")
    architecture_docx(OUT / "Lumora_Platform_Architecture.docx")
    (OUT / "lumora_it_helpdesk_faq.html").write_text(HELPDESK_HTML, encoding="utf-8")
    print(f"Sample documents written to {OUT}")


if __name__ == "__main__":
    main()
