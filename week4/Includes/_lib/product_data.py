"""Course data for Get Started with Databricks for Generative AI.

Generates the synthetic e-commerce product catalog and picks the products that
get a spec sheet (_lib/spec_sheet_pdf_creation.py writes the PDFs). Workspace-Setup
calls these once per workspace. A fixed seed means every workspace gets the
same products, so lesson outputs and sample questions match.
"""

import random
import string
from datetime import datetime, timedelta

SEED = 42
NUM_PRODUCTS = 1000

PRODUCT_KEYWORDS = [
    "Wireless", "Bluetooth", "Smart", "Portable", "UltraSlim",
    "Rechargeable", "4K", "Noise Cancelling", "Ergonomic", "Stainless Steel",
    "Foldable", "Adjustable", "High Precision", "Touchscreen", "LED",
]

PRODUCT_ITEMS = [
    "Headphones", "Speaker", "Air Purifier", "Keyboard", "Mouse",
    "Coffee Maker", "Fitness Tracker", "Power Bank", "Smart Lamp",
    "Water Bottle", "Laptop Stand", "Travel Bag", "Microphone",
    "Webcam", "Projector", "Juicer", "Hair Dryer",
]

BRAND_PREFIXES = [
    "Nova", "Prime", "Aero", "Luma", "Vertex", "Pixel", "Urban",
    "Zenith", "Echo", "Nimbus", "Fusion", "Astra", "Quantum",
    "Vista", "Polar", "Terra",
]

BRAND_SUFFIXES = [
    "Tech", "Labs", "Works", "Gear", "House", "Studio",
    "Core", "Wave", "Systems", "Designs", "Essentials",
    "Line", "World", "Hub", "Select",
]

DESCRIPTION_TEMPLATES = [
    (
        "{title} from {brand} is intended as a practical option for people who want something reliable "
        "without adding unnecessary complexity to their daily routine. It focuses on straightforward operation, "
        "so you do not need any special experience to start using it, and it fits easily into a variety of home "
        "or office setups. The overall build aims to balance durability with a clean, neutral appearance. "
        "Key aspects include {features}, combined to offer a balanced experience that feels easy to live with over time."
    ),
    (
        "{brand} created the {title} for users who prefer dependable tools that stay out of the way and simply do "
        "their job. Rather than focusing on flashy add-ons, the product emphasizes a clear layout and familiar "
        "controls, so it feels approachable even on the first use. Core design points include {features}, which are "
        "grouped together to prioritize ease of use, steady performance, and a calm presence in day-to-day life."
    ),
    (
        "The {title} offered by {brand} is shaped around the idea that a useful product does not need to be complicated "
        "to be effective. It is meant for people who want something they can set up once and then trust during regular "
        "work or home activities. Features such as {features} are included to support this general-purpose approach, "
        "offering a mix of practicality, familiarity, and low-effort upkeep."
    ),
    (
        "{brand}'s {title} is designed around everyday patterns of use, where reliability and clarity matter more than "
        "constant adjustment. Its controls and behavior are easy to understand from the moment you take it out of the box, "
        "and its neutral style blends into different environments. The inclusion of {features} is aimed at supporting "
        "daily tasks while keeping the overall experience calm and manageable."
    ),
    (
        "With the {title}, {brand} aims to offer a practical companion for routine tasks, whether you are at home or at work. "
        "The product is structured so that you can start using it with minimal preparation, and its proportions are chosen to "
        "fit comfortably into compact spaces. The set of characteristics grouped under {features} reflects this philosophy, "
        "focusing on ease of handling, simple upkeep, and consistent performance."
    ),
]

FEATURE_TEMPLATES = [
    "Lightweight design; Easy to clean; Suitable for daily use",
    "Compact form factor; Simple controls; Works well in home or office",
    "Durable build; Stable performance; Low maintenance",
    "Quick setup; Clear instructions; Designed for everyday tasks",
    "Adjustable settings; Quiet operation; Space-saving design",
    "Energy-conscious operation; Reliable components; Minimal setup",
    "Portable size; User-friendly layout; Supports regular use",
    "Sturdy construction; Balanced design; Straightforward handling",
    "Smooth operation; Neutral finish; Blends into most spaces",
    "Non-slip base; Practical layout; Optimized for regular workloads",
]

CURRENCIES = ["USD", "INR", "EUR", "GBP", "CAD", "AUD"]
AVAILABILITY = ["In Stock", "Limited Stock", "Out of Stock", "Available Soon", "Preorder Available"]
CATEGORIES = [
    "Electronics > Audio > Headphones",
    "Electronics > Computers > Accessories",
    "Electronics > Home Audio > Speakers",
    "Home & Kitchen > Small Appliances > Coffee Makers",
    "Home & Kitchen > Storage & Organization",
    "Home & Kitchen > Lighting > Table Lamps",
    "Sports & Outdoors > Fitness > Accessories",
    "Sports & Outdoors > Camping & Hiking > Gear",
    "Beauty & Personal Care > Hair Care > Styling",
    "Beauty & Personal Care > Skin Care > Tools",
    "Office Products > Office Electronics > Printers",
    "Office Products > Furniture > Chairs",
    "Automotive > Interior Accessories",
    "Toys & Games > Learning & Education",
    "Toys & Games > Puzzles",
    "Industrial & Scientific > Test & Measurement",
]
ROOT_CATEGORIES = [
    "Electronics", "Home & Kitchen", "Computers & Accessories", "Sports & Outdoors",
    "Beauty & Personal Care", "Toys & Games", "Automotive", "Office Products", "Industrial & Scientific",
]
DOMAINS = [
    "shoplite.store", "megabuyhub.com", "trendifyonline.net", "primegoodsmart.co", "dealstreak.io",
    "retailnova.com", "quickmartdirect.org", "brightcart.net", "hyperretail.co", "urbangoods.shop",
]
WEIGHTS = ["250g", "500g", "750g", "1kg", "1.2kg", "1.5kg", "2kg", "2.5kg", "3kg"]
DELIVERY = [
    "Delivered within 3-5 business days", "Delivered within 5-7 business days", "Expected delivery: 2-4 days",
    "Ships within 48 hours", "Arrives next week", "Delivery estimate: 6-10 days",
    "Fast delivery available", "Standard delivery only",
]
DISCOUNTS = [0, 5, 10, 12, 15, 20, 25, 30, 18]
MANUFACTURERS = [
    "LumaTek Industries", "Vertex Global Manufacturing", "AeroCore Appliances", "NovaLine Electronics",
    "PolarEdge Systems", "TerraCraft Labs", "FusionPoint Devices", "EchoStone Technologies",
    "Zenora Home Products", "BrightWave Solutions", "UrbanAxis Hardware", "NimbusPeak Innovations",
]

def _random_date(rng, start, end):
    start = datetime.strptime(start, "%Y-%m-%d")
    end = datetime.strptime(end, "%Y-%m-%d")
    return start + timedelta(days=rng.randrange((end - start).days), seconds=rng.randrange(86400))


def _code(rng, alphabet, k):
    return "".join(rng.choices(alphabet, k=k))


def generate_products(num_rows=NUM_PRODUCTS, seed=SEED):
    """Return the product catalog as a list of dicts (one per product)."""
    rng = random.Random(seed)
    rows = []
    for _ in range(num_rows):
        title = f"{rng.choice(PRODUCT_KEYWORDS)} {rng.choice(PRODUCT_ITEMS)}"
        brand = rng.choice(BRAND_PREFIXES) + rng.choice(BRAND_SUFFIXES)
        features = rng.choice(FEATURE_TEMPLATES)
        initial_price = round(rng.uniform(10, 999), 2)
        discount = rng.choice(DISCOUNTS)
        domain = rng.choice(DOMAINS)
        slug = f"{rng.choice(['portable', 'wireless', 'smart', 'premium', 'compact', 'eco'])}-product-{rng.randint(1000, 9999)}"
        rows.append({
            "Timestamp": _random_date(rng, "2020-01-01", "2024-12-01"),
            "Title": title,
            "Brand": brand,
            "Description": rng.choice(DESCRIPTION_TEMPLATES).format(title=title, brand=brand, features=features),
            "Initial_Price": initial_price,
            "Final_Price": float(max(1, round(initial_price * (1 - discount / 100.0)))),
            "Currency": rng.choice(CURRENCIES),
            "Availability": rng.choice(AVAILABILITY),
            "Review_Count": rng.randint(0, 50000),
            "Categories": rng.choice(CATEGORIES),
            "ASIN": _code(rng, string.ascii_uppercase + string.digits, 10),
            "Number_of_Sellers": rng.randint(2, 10),
            "Root_BS_Rank": f"#{rng.randint(1, 500000)} in {rng.choice(ROOT_CATEGORIES)}",
            "Domain": domain,
            "URL": f"https://{domain}/product/{slug}/",
            "Item_Weight": rng.choice(WEIGHTS),
            "Delivery": rng.choice(DELIVERY),
            "Rating": rng.randint(2, 10) / 2.0,
            "Product_Dimensions": f"{rng.randint(5, 50)} x {rng.randint(3, 30)} x {rng.randint(1, 25)} cm",
            "Date_First_Available": _random_date(rng, "2018-01-01", "2024-12-01").strftime("%Y-%m-%d"),
            "Discount": f"{discount}% off" if discount else "No discount",
            "Features": features,
            "Model_Number": f"{_code(rng, string.ascii_uppercase, 2)}-{rng.randint(100, 999)}{rng.choice(string.ascii_uppercase)}",
            "Manufacturer": rng.choice(MANUFACTURERS),
        })
    return rows


def spec_sheet_products(rows, per_item=2):
    """Pick a few products per item type, in catalog order, to get a spec sheet PDF."""
    picked, counts = [], {}
    for row in rows:
        item = next(i for i in PRODUCT_ITEMS if row["Title"].endswith(i))
        if counts.get(item, 0) < per_item:
            counts[item] = counts.get(item, 0) + 1
            picked.append((item, row))
    return picked
