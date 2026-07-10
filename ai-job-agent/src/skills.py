"""Generic, categorized skill dictionaries used by the resume parser and matcher.

Each category maps a *canonical* skill name (the nice display form) to a list of
lowercase aliases that might appear in resume or job text. Detection is
case-insensitive and uses word boundaries so short aliases like "go" or "excel"
do not match inside larger words ("going", "excellent").

To extend the parser for a new field, just add entries here.
"""

import re

# category -> { canonical_name: [aliases...] }
SKILL_CATEGORIES: dict[str, dict[str, list[str]]] = {
    "programming_languages": {
        "Python": ["python"],
        "JavaScript": ["javascript"],
        "TypeScript": ["typescript"],
        "Java": ["java"],
        "C++": ["c++"],
        "C#": ["c#", ".net", "dotnet"],
        "C": ["c language", "c programming"],
        "Go": ["golang", "go programming"],
        "PHP": ["php"],
        "Ruby": ["ruby"],
        "Swift": ["swift"],
        "Kotlin": ["kotlin"],
        "SQL": ["sql"],
        "R": ["r programming", "rstudio", "r language"],
        "MATLAB": ["matlab"],
        "Scala": ["scala"],
        "Rust": ["rust"],
    },
    "frameworks_libraries": {
        "React": ["react", "react.js", "reactjs"],
        "React Native": ["react native"],
        "Angular": ["angular"],
        "Vue": ["vue", "vue.js", "vuejs"],
        "Node.js": ["node.js", "nodejs", "node"],
        "Express": ["express", "express.js"],
        "FastAPI": ["fastapi"],
        "Django": ["django"],
        "Flask": ["flask"],
        "Laravel": ["laravel"],
        "Spring": ["spring boot", "spring framework"],
        ".NET": [".net framework", "asp.net"],
        "TensorFlow": ["tensorflow"],
        "PyTorch": ["pytorch"],
        "SQLAlchemy": ["sqlalchemy"],
        "WebSockets": ["websocket", "websockets"],
        "Expo": ["expo"],
        "jQuery": ["jquery"],
        "Bootstrap": ["bootstrap"],
        "Next.js": ["next.js", "nextjs"],
        "HTML": ["html", "html5"],
        "CSS": ["css", "css3", "sass", "tailwind"],
        "REST API": ["rest api", "rest apis", "restful", "rest"],
    },
    "databases": {
        "PostgreSQL": ["postgresql", "postgres"],
        "MySQL": ["mysql"],
        "MongoDB": ["mongodb", "mongo"],
        "Firebase": ["firebase"],
        "SQLite": ["sqlite"],
        "Oracle": ["oracle"],
        "SQL Server": ["sql server", "mssql"],
        "Redis": ["redis"],
        "Elasticsearch": ["elasticsearch", "elastic search"],
    },
    "cloud_devops_tools": {
        "AWS": ["aws", "amazon web services"],
        "Azure": ["azure"],
        "GCP": ["gcp", "google cloud"],
        "Docker": ["docker"],
        "Kubernetes": ["kubernetes", "k8s"],
        "Git": ["git", "github", "gitlab"],
        "CI/CD": ["ci/cd", "cicd"],
        "Jenkins": ["jenkins"],
        "Terraform": ["terraform"],
        "Linux": ["linux", "ubuntu", "לינוקס"],
        "Nginx": ["nginx"],
        "Bash": ["bash", "shell scripting"],
        "PowerShell": ["powershell"],
    },
    "data_ai": {
        "Machine Learning": ["machine learning", "ml"],
        "Deep Learning": ["deep learning"],
        "LLM": ["llm", "large language model", "large language models"],
        "Generative AI": ["generative ai", "gen ai", "genai"],
        "NLP": ["nlp", "natural language processing"],
        "Computer Vision": ["computer vision"],
        "Pandas": ["pandas"],
        "NumPy": ["numpy"],
        "Power BI": ["power bi"],
        "Tableau": ["tableau"],
        "Data Analysis": ["data analysis", "data analytics"],
        "Data Science": ["data science"],
        "scikit-learn": ["scikit-learn", "sklearn"],
    },
    "cyber_security": {
        "SOC": ["soc", "security operations"],
        "SIEM": ["siem"],
        "Splunk": ["splunk"],
        "QRadar": ["qradar"],
        "EDR": ["edr"],
        "XDR": ["xdr"],
        "Firewall": ["firewall", "פיירוול", "חומת אש"],
        "IDS": ["ids"],
        "IPS": ["ips"],
        "Networking": ["networking", "network administration", "network engineering", "רשתות מחשבים"],
        "TCP/IP": ["tcp/ip", "dns", "dhcp"],
        "Incident Response": ["incident response"],
        "Cybersecurity": ["cybersecurity", "cyber", "סייבר", "אבטחת מידע"],
        "Penetration Testing": ["penetration testing", "pentest", "מבדקי חדירות"],
    },
    "design_creative": {
        "Figma": ["figma"],
        "Photoshop": ["photoshop"],
        "Illustrator": ["illustrator"],
        "InDesign": ["indesign"],
        "After Effects": ["after effects"],
        "UX": ["ux design", "user experience design", "ux designer"],
        "UI": ["ui design", "user interface design", "ui designer", "ux/ui"],
        "Canva": ["canva"],
        "Branding": ["branding"],
        "Video Editing": ["video editing", "premiere", "final cut"],
    },
    "marketing_sales": {
        "SEO": ["seo"],
        "PPC": ["ppc"],
        "Google Ads": ["google ads", "adwords"],
        "Meta Ads": ["meta ads", "facebook ads"],
        "CRM": ["crm", "salesforce", "hubspot"],
        "Sales": ["sales", "מכירות"],
        "B2B": ["b2b"],
        "B2C": ["b2c"],
        "Lead Generation": ["lead generation"],
        "Copywriting": ["copywriting", "קופירייטינג"],
        "Email Marketing": ["email marketing"],
        "Social Media": ["social media", "מדיה חברתית"],
    },
    "finance_accounting": {
        "Bookkeeping": ["bookkeeping", "הנהלת חשבונות"],
        "Payroll": ["payroll", "שכר"],
        "Financial Analysis": ["financial analysis", "ניתוח פיננסי"],
        "QuickBooks": ["quickbooks"],
        "ERP": ["erp"],
        "SAP": ["sap"],
        "Accounting": ["accounting", "חשבונאות"],
        "Budgeting": ["budgeting", "budget management"],
    },
    "operations_logistics": {
        "Inventory": ["inventory", "מלאי"],
        "Supply Chain": ["supply chain", "שרשרת אספקה"],
        "Procurement": ["procurement", "רכש"],
        "Warehouse": ["warehouse", "מחסן"],
        "Shipping": ["shipping", "משלוחים"],
        "Import": ["import", "יבוא"],
        "Export": ["export", "יצוא"],
        "Scheduling": ["scheduling"],
        "Logistics": ["logistics", "לוגיסטיקה"],
    },
    "hr_admin": {
        "Recruiting": ["recruiting", "recruitment", "גיוס"],
        "Onboarding": ["onboarding", "קליטת עובדים"],
        "Office Management": ["office management", "ניהול משרד"],
        "Administration": ["administration", "administrative", "אדמיניסטרציה"],
        "Human Resources": ["human resources", "משאבי אנוש"],
    },
    "healthcare": {
        "Obstetrics": ["obstetrics", "obstetrician", "מיילדות"],
        "Gynaecology": ["gynaecology", "gynecology", "gynaecologist", "gynecologist", "גינקולוגיה"],
        "Surgery": ["surgery", "surgical", "surgeon", "ניתוח", "מנתח"],
        "Urogynaecology": ["urogynaecology", "urogynecology"],
        "Medicine": ["medicine", "medical practice", "physician", "רפואה", "רופא"],
        "Patient Care": ["patient care", "טיפול בחולים"],
        "Nursing": ["nursing", "nurse", "סיעוד", "אחות", "אח"],
        "Medical Records": ["medical records", "רשומות רפואיות"],
        "Clinical": ["clinical", "קליני"],
        "Public Health": ["public health", "mph", "בריאות הציבור"],
        "CPR": ["cpr", "החייאה"],
        "First Aid": ["first aid", "עזרה ראשונה"],
    },
    "languages": {
        "English": ["english", "אנגלית"],
        "Hebrew": ["hebrew", "עברית"],
        "Arabic": ["arabic", "ערבית"],
        "Russian": ["russian", "רוסית"],
        "French": ["french", "צרפתית"],
        "Spanish": ["spanish", "ספרדית"],
        "German": ["german", "גרמנית"],
        "Amharic": ["amharic", "אמהרית"],
        "Portuguese": ["portuguese"],
        "Chinese": ["chinese", "mandarin"],
    },
    "soft_skills": {
        "Leadership": ["leadership", "מנהיגות"],
        "Communication": ["communication", "תקשורת בינאישית"],
        "Problem Solving": ["problem solving", "problem-solving"],
        "Teamwork": ["teamwork", "team work", "עבודת צוות"],
        "Customer Service": ["customer service", "שירות לקוחות"],
        "Training": ["training", "הדרכה"],
        "Mentoring": ["mentoring", "mentorship", "חניכה"],
        "Time Management": ["time management", "ניהול זמן"],
        "Adaptability": ["adaptability", "flexibility"],
    },
    "general_tools": {
        "Microsoft Office": ["microsoft office", "ms office", "office suite"],
        "Excel": ["excel"],
        "Word": ["microsoft word", "ms word"],
        "PowerPoint": ["powerpoint", "power point"],
        "Outlook": ["outlook"],
        "Google Workspace": ["google workspace", "g suite", "google docs"],
        "Notion": ["notion"],
        "Jira": ["jira"],
        "Trello": ["trello"],
        "Slack": ["slack"],
        "Zoom": ["zoom"],
    },
    # Reserved catch-all; the parser leaves this empty unless extended.
    "other": {},
}


def _alias_in_text(alias: str, text_l: str) -> bool:
    """Return True if an alias appears as a standalone token in lowercased text.

    English letters/digits are treated as word characters so that, for example,
    "go" does not match inside "going" and "excel" does not match "excellent".
    Hebrew characters are not in [a-z0-9], so Hebrew aliases match naturally.
    """
    pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
    return re.search(pattern, text_l) is not None


def detect_skills_by_category(text: str) -> dict[str, list[str]]:
    """Return canonical skills found in the text, grouped by category."""
    text_l = (text or "").lower()
    result: dict[str, list[str]] = {category: [] for category in SKILL_CATEGORIES}

    for category, skills in SKILL_CATEGORIES.items():
        for canonical, aliases in skills.items():
            if any(_alias_in_text(alias, text_l) for alias in aliases):
                result[category].append(canonical)

    return result


def detect_skills(text: str) -> list[str]:
    """Return a flat list of all canonical skills found in the text."""
    flat: list[str] = []
    for items in detect_skills_by_category(text).values():
        flat.extend(items)
    return flat
