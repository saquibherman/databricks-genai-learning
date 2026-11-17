"""Product spec sheet PDFs for Get Started with Databricks for Generative AI.

Course-specific data creation: read products from the product catalog table
and write one PDF spec sheet per product into a volume. Modeled on
pdf_creation.py; a general one-PDF-per-record helper can replace the PDF code
here later.
"""

import os

from pyspark.sql import SparkSession
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

spark = SparkSession.builder.getOrCreate()

# Warranty and returns differ by manufacturer, so a Genie Agent has to read the
# right PDF to answer questions like "What is the warranty on the Smart Lamp
# from NovaLine?"
WARRANTY_MONTHS = {
    "LumaTek Industries": 12,
    "Vertex Global Manufacturing": 24,
    "AeroCore Appliances": 12,
    "NovaLine Electronics": 36,
    "PolarEdge Systems": 24,
    "TerraCraft Labs": 12,
    "FusionPoint Devices": 18,
    "EchoStone Technologies": 24,
    "Zenora Home Products": 12,
    "BrightWave Solutions": 36,
    "UrbanAxis Hardware": 18,
    "NimbusPeak Innovations": 24,
}

RETURN_DAYS = {
    "LumaTek Industries": 30,
    "Vertex Global Manufacturing": 30,
    "AeroCore Appliances": 14,
    "NovaLine Electronics": 60,
    "PolarEdge Systems": 30,
    "TerraCraft Labs": 30,
    "FusionPoint Devices": 14,
    "EchoStone Technologies": 45,
    "Zenora Home Products": 30,
    "BrightWave Solutions": 60,
    "UrbanAxis Hardware": 30,
    "NimbusPeak Innovations": 14,
}

# One care tip per product type. The keys are the item types in the catalog,
# which every product title ends with (e.g. "Touchscreen Air Purifier").
CARE_TIPS = {
    "Headphones": "Wipe the ear cushions with a dry cloth. Store in the case to protect the headband.",
    "Speaker": "Keep the speaker grille free of dust. Avoid placing it in direct sunlight for long periods.",
    "Air Purifier": "Replace the filter every 6 months. Vacuum the pre-filter monthly.",
    "Keyboard": "Turn the keyboard upside down and shake gently to remove debris. Clean keycaps with a damp cloth.",
    "Mouse": "Clean the sensor window with a dry cotton swab. Replace batteries when the LED blinks red.",
    "Coffee Maker": "Descale every 3 months with a 1:1 vinegar and water solution. Rinse twice afterwards.",
    "Fitness Tracker": "Rinse the band with fresh water after workouts. Charge at least once a week.",
    "Power Bank": "Store at 50% charge if unused for more than a month. Avoid temperatures above 45 C.",
    "Smart Lamp": "Dust the shade weekly. Use only the supplied power adapter.",
    "Water Bottle": "Hand wash only. Do not put the lid in the dishwasher.",
    "Laptop Stand": "Check and tighten the hinge screws every few months.",
    "Travel Bag": "Spot clean with mild soap. Do not machine wash.",
    "Microphone": "Use the foam windscreen to protect the capsule. Store away from humidity.",
    "Webcam": "Clean the lens with a microfiber cloth. Keep firmware up to date through the companion app.",
    "Projector": "Clean the air vents monthly. Let the unit cool for 5 minutes before unplugging.",
    "Juicer": "Disassemble and rinse immediately after use. The blade assembly is dishwasher safe.",
    "Hair Dryer": "Clean the rear air filter every 2 weeks. Do not wrap the cord tightly around the handle.",
}

TROUBLESHOOTING = [
    "Product does not power on: check the power source and make sure the unit is fully charged or plugged in.",
    "Wireless connection drops: move the product within 10 meters of the paired device and remove obstacles.",
    "Unusual noise: turn the product off, check for loose parts, and contact support if the noise continues.",
    "Settings reset unexpectedly: perform a factory reset by holding the power button for 10 seconds.",
]


def spec_sheet_file_name(row) -> str:
    """Return the spec sheet file name for a product, e.g. AeroHub_Bluetooth_Coffee_Maker_AG-925T.pdf."""
    return f"{row['Brand']}_{row['Title']}_{row['Model_Number']}.pdf".replace(" ", "_")


def _item_type(title: str) -> str:
    """Return the product type (a CARE_TIPS key) that a product title ends with."""
    return next(item for item in CARE_TIPS if title.endswith(item))


def write_spec_sheet_pdf(row, output_path: str) -> str:
    """
    Write one product spec sheet PDF.

    The spec sheet has an overview, a specifications table, key features, care
    and maintenance, warranty and returns, and troubleshooting.

    Parameters
    ----------
    row : dict
        One product, with the product catalog columns (Brand, Title,
        Description, Features, Manufacturer, Model_Number, Categories,
        Product_Dimensions, Item_Weight, ASIN).
    output_path : str
        Destination path for the PDF file.

    Returns
    -------
    str
        Path where the PDF was written.
    """
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output_path, pagesize=letter, leftMargin=0.85 * inch, rightMargin=0.85 * inch)
    manufacturer = row["Manufacturer"]
    specs = [
        ["Model number", row["Model_Number"]],
        ["Brand", row["Brand"]],
        ["Manufacturer", manufacturer],
        ["Category", row["Categories"]],
        ["Dimensions", row["Product_Dimensions"]],
        ["Weight", row["Item_Weight"]],
        ["Product ID (ASIN)", row["ASIN"]],
    ]
    table = Table(specs, colWidths=[1.8 * inch, 4.4 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF2F7")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9D1DC")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story = [
        Paragraph(f"{row['Brand']} {row['Title']}", styles["Title"]),
        Paragraph("Product Specification Sheet", styles["Heading3"]),
        Spacer(1, 8),
        Paragraph("Overview", styles["Heading2"]),
        Paragraph(row["Description"], styles["BodyText"]),
        Paragraph("Specifications", styles["Heading2"]),
        table,
        Paragraph("Key features", styles["Heading2"]),
        Paragraph("<br/>".join(f"- {f.strip()}" for f in row["Features"].split(";")), styles["BodyText"]),
        Paragraph("Care and maintenance", styles["Heading2"]),
        Paragraph(CARE_TIPS[_item_type(row["Title"])], styles["BodyText"]),
        Paragraph("Warranty and returns", styles["Heading2"]),
        Paragraph(
            f"{manufacturer} covers this product with a {WARRANTY_MONTHS[manufacturer]}-month limited warranty "
            f"against manufacturing defects. Unused products can be returned within {RETURN_DAYS[manufacturer]} days "
            f"of delivery in the original packaging.",
            styles["BodyText"],
        ),
        Paragraph("Troubleshooting", styles["Heading2"]),
        Paragraph("<br/>".join(f"- {t}" for t in TROUBLESHOOTING), styles["BodyText"]),
    ]
    doc.build(story)
    return output_path


def create_spec_sheet_pdfs(
    table_name: str,
    output_dir: str,
    product_ids: list = None,
    overwrite: bool = False,
) -> list:
    """
    Read products from a Delta table and write one spec sheet PDF per product.

    Parameters
    ----------
    table_name : str
        Fully-qualified table with the product catalog columns, such as
        dbacademy.get_started_genai.product_listings.
    output_dir : str
        Destination folder, usually a volume path: /Volumes/<catalog>/<schema>/<volume>.
    product_ids : list[str], optional
        ASINs of the products to write spec sheets for. Default: every product
        in the table.
    overwrite : bool
        Rewrite spec sheets that already exist. Default: False, so re-running
        only writes missing files.

    Returns
    -------
    list[str]
        Paths of the spec sheets for the selected products.
    """
    df = spark.table(table_name)
    if product_ids is not None:
        df = df.where(df.ASIN.isin(list(product_ids)))
    rows = [r.asDict() for r in df.collect()]
    print(f"  Loaded {len(rows)} products from {table_name}")

    if product_ids is not None:
        missing = set(product_ids) - {r["ASIN"] for r in rows}
        if missing:
            print(f"  Warning: {len(missing)} product ID(s) not found in {table_name}: {sorted(missing)}")

    os.makedirs(output_dir, exist_ok=True)
    existing = set(os.listdir(output_dir))
    paths, written = [], 0
    for row in rows:
        file_name = spec_sheet_file_name(row)
        path = os.path.join(output_dir, file_name)
        if overwrite or file_name not in existing:
            write_spec_sheet_pdf(row, path)
            written += 1
        paths.append(path)

    print(f"  Spec sheets in {output_dir}: {len(paths)} ({written} written)")
    return paths
