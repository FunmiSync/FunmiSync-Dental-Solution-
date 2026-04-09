from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer


OUTPUT_PATH = Path("docs/Ayomide_Ganiyu_ENDGAME_CV.pdf")


CONTACT_LINE = (
    "Abuja, Nigeria | 09039276706 | ganiyuaa2019@gmail.com | "
    "linkedin.com/in/ayomide-ganiyu-52658824b | github.com/AYTIPS"
)


SUMMARY = (
    "Backend engineer with 5+ years of experience building Python-based backend systems, "
    "integration workflows, and operational tooling across FastAPI, Flask, Django, PostgreSQL, "
    "Redis, AWS, and GCP. Strongest in API design, event-driven processing, async workflows, "
    "multi-tenant backend design, and production-minded systems that need reliability, observability, "
    "and clear operational behavior. Comfortable turning ambiguous requirements into shipped backend "
    "systems and improving them with pragmatic caching, background jobs, and workflow automation."
)


CORE_STRENGTHS = [
    "Backend Systems: Python, FastAPI, Flask, Django, SQLAlchemy, REST APIs, Webhooks, Async Services",
    "Data and Storage: PostgreSQL, MySQL, MongoDB, Redis, ETL Pipelines, Query Optimization",
    "Platform and Cloud: AWS Lambda, GCP Functions, Docker, Kubernetes, Terraform, CI/CD",
    "Architecture: Multi-tenant Access Control, Event-Driven Workflows, Operational Dashboards, Caching",
    "Integration Work: CRM Integrations, Google APIs, Shopify, Automation Pipelines, Sync Workflows",
    "Delivery: Production Debugging, API Reliability, Written Technical Thinking, AI-Assisted Engineering",
]


EXPERIENCE = [
    {
        "title": "Backend Engineer & Data Integration Developer",
        "company": "Celebrate Dentals, Remote",
        "dates": "Apr 2025 - Sept 2025",
        "bullets": [
            "Engineered FastAPI and Flask APIs that moved operational data between CRM systems and internal services.",
            "Built asynchronous Python services for real-time synchronization across Shopify, GoHighLevel, Google APIs, and internal workflows.",
            "Designed and optimized ETL-style data pipelines processing 1M+ daily records while maintaining 99.9% data consistency.",
            "Automated data movement and backend workflows on AWS Lambda and GCP Cloud Functions, reducing manual operations by 80%.",
            "Improved delivery speed and deployment reliability with Docker-based services and CI/CD workflows.",
        ],
    },
    {
        "title": "AI & Machine Learning Engineer (Freelance)",
        "company": "Stratify, Remote",
        "dates": "2019 - 2022",
        "bullets": [
            "Deployed and maintained ML-backed services for recommendations, text analytics, and predictive tasks.",
            "Integrated AI APIs into backend systems for chatbot and automation workflows.",
            "Improved model inference performance by 25% through production tuning with TensorFlow and PyTorch.",
            "Designed FastAPI-based model-serving endpoints and Dockerized services for scalable deployment.",
        ],
    },
    {
        "title": "Backend Engineer & API Integrations (Freelance)",
        "company": "Ratedby10, Remote",
        "dates": "2017 - 2019",
        "bullets": [
            "Built Python APIs for automation tools and SaaS products using Flask and FastAPI.",
            "Designed normalized PostgreSQL and MySQL schemas and improved query performance for operational workloads.",
            "Implemented backend middleware for logging, caching, and error handling, improving uptime by 40%.",
            "Automated synchronization flows across CRM and eCommerce systems, reducing data discrepancies by 40%.",
        ],
    },
]


PROJECTS = [
    {
        "name": "Multi-Clinic Dental Operations Platform",
        "details": [
            "Building a FastAPI-based multi-tenant backend for DSO and clinic operations with scoped RBAC, invite-based onboarding, workspace routing, and secure authentication.",
            "Designed sync-log services with cursor pagination, date-range filtering, Redis-backed event notifications, SSE streams, and production-oriented caching strategy.",
            "Implemented workflow-oriented backend patterns around webhook ingestion, async processing, operational visibility, and audit-friendly sync behavior.",
        ],
    },
    {
        "name": "Data Automation Framework",
        "details": [
            "Designed a serverless data processing framework with FastAPI, AWS Lambda, and PostgreSQL that reduced reporting time by 70%.",
        ],
    },
    {
        "name": "API Gateway Integration",
        "details": [
            "Implemented a unified API gateway pattern for cross-service authentication, request handling, and rate limiting.",
        ],
    },
]


EDUCATION = [
    "B.Sc., Soil Science - Obafemi Awolowo University, Ile Ife, Osun State (2024)",
]


CERTIFICATIONS = [
    "ALX Software Engineering Certificate",
    "Google Digital Skills for Africa",
    "AWS Cloud Practitioner (in progress)",
]


ADDITIONAL = [
    "Stratify Innovation Award (2025): Recognized for automating patient data pipelines.",
    "Top-Rated Freelancer (Upwork): Consistent 5-star client feedback across 20+ backend and data projects.",
]


def build_styles():
    styles = getSampleStyleSheet()

    styles.add(
        ParagraphStyle(
            name="Name",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#111827"),
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Contact",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=12,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#374151"),
            spaceAfter=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionHeader",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=14,
            textColor=colors.HexColor("#0F172A"),
            spaceBefore=6,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Body",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=12.5,
            textColor=colors.black,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Role",
            parent=styles["Body"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12.5,
            textColor=colors.HexColor("#111827"),
            spaceAfter=1,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Meta",
            parent=styles["Body"],
            fontName="Helvetica-Oblique",
            fontSize=9,
            leading=11.5,
            textColor=colors.HexColor("#4B5563"),
            spaceAfter=4,
        )
    )
    return styles


def bullets(items, styles):
    return ListFlowable(
        [
            ListItem(Paragraph(item, styles["Body"]), leftIndent=0)
            for item in items
        ],
        bulletType="bullet",
        leftIndent=12,
        bulletFontName="Helvetica",
        bulletFontSize=8,
        bulletOffsetY=1,
        spaceBefore=0,
        spaceAfter=6,
    )


def build_pdf():
    styles = build_styles()
    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=A4,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
    )

    story = [
        Paragraph("AYOMIDE GANIYU", styles["Name"]),
        Paragraph(CONTACT_LINE, styles["Contact"]),
        Paragraph("BACKEND ENGINEER | INTEGRATIONS, PLATFORM SYSTEMS, AND PYTHON", styles["Role"]),
        Spacer(1, 8),
        Paragraph("PROFESSIONAL SUMMARY", styles["SectionHeader"]),
        Paragraph(SUMMARY, styles["Body"]),
        Paragraph("CORE STRENGTHS", styles["SectionHeader"]),
        bullets(CORE_STRENGTHS, styles),
        Paragraph("PROFESSIONAL EXPERIENCE", styles["SectionHeader"]),
    ]

    for role in EXPERIENCE:
        story.append(Paragraph(role["title"], styles["Role"]))
        story.append(Paragraph(f'{role["company"]} | {role["dates"]}', styles["Meta"]))
        story.append(bullets(role["bullets"], styles))

    story.append(Paragraph("SELECTED PROJECTS", styles["SectionHeader"]))
    for project in PROJECTS:
        story.append(Paragraph(project["name"], styles["Role"]))
        story.append(bullets(project["details"], styles))

    story.append(Paragraph("EDUCATION", styles["SectionHeader"]))
    story.append(bullets(EDUCATION, styles))

    story.append(Paragraph("CERTIFICATIONS", styles["SectionHeader"]))
    story.append(bullets(CERTIFICATIONS, styles))

    story.append(Paragraph("ADDITIONAL", styles["SectionHeader"]))
    story.append(bullets(ADDITIONAL, styles))

    doc.build(story)


if __name__ == "__main__":
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    build_pdf()
    print(OUTPUT_PATH.resolve())
