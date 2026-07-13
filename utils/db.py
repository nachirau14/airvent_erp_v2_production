"""
Database layer — DynamoDB + S3 operations.
Reads AWS credentials from Streamlit secrets.
"""
import boto3
import uuid
import json
import io
import streamlit as st
from datetime import datetime, timedelta
from decimal import Decimal
from boto3.dynamodb.conditions import Key, Attr
from config import TABLES

# S3 bucket names
S3_PO_PDF_BUCKET = "fabriflow-po-pdfs"          # Auto-empties after 30 days
S3_ATTACHMENTS_BUCKET = "fabriflow-attachments"  # Persistent storage


# ─── AWS Connection ───────────────────────────────────────────────
def _get_aws_config():
    aws = st.secrets.get("aws", {})
    return {
        "aws_access_key_id": aws.get("AWS_ACCESS_KEY_ID", ""),
        "aws_secret_access_key": aws.get("AWS_SECRET_ACCESS_KEY", ""),
        "region_name": aws.get("AWS_REGION", "ap-south-1"),
    }

@st.cache_resource
def get_dynamodb():
    return boto3.resource("dynamodb", **_get_aws_config())

@st.cache_resource
def get_s3_client():
    return boto3.client("s3", **_get_aws_config())

@st.cache_resource
def get_sqs_client():
    return boto3.client("sqs", **_get_aws_config())

def _gen_id(prefix=""):
    return f"{prefix}{uuid.uuid4().hex[:12]}"

def _now():
    return datetime.utcnow().isoformat()

def _today():
    return datetime.utcnow().strftime("%Y-%m-%d")

def _financial_year_prefix():
    """Return financial year prefix like FY2526 for Apr 2025 - Mar 2026."""
    now = datetime.utcnow()
    fy_start = now.year if now.month >= 4 else now.year - 1
    fy_end = fy_start + 1
    return f"{str(fy_start)[2:]}{str(fy_end)[2:]}"

COUNTER_TABLE = "erp_counters"

def _next_sequential_id(prefix, counter_name=None):
    """Get next sequential ID like MI-0001, RMPO-FY2526-0001.
    Uses an atomic counter in DynamoDB for concurrency safety."""
    if counter_name is None:
        counter_name = prefix.rstrip("-")
    db = get_dynamodb()
    table = db.Table(COUNTER_TABLE)
    try:
        resp = table.update_item(
            Key={"counter_name": counter_name},
            UpdateExpression="SET counter_value = if_not_exists(counter_value, :zero) + :inc",
            ExpressionAttributeValues={":inc": 1, ":zero": 0},
            ReturnValues="UPDATED_NEW",
        )
        seq = int(resp["Attributes"]["counter_value"])
    except Exception:
        # Fallback if counter table doesn't exist
        return _gen_id(prefix)
    return f"{prefix}{seq:04d}"

def _next_po_id(prefix):
    """Sequential PO ID per financial year: RMPO-FY2526-0001."""
    fy = _financial_year_prefix()
    counter_name = f"{prefix.rstrip('-')}-FY{fy}"
    db = get_dynamodb()
    table = db.Table(COUNTER_TABLE)
    try:
        resp = table.update_item(
            Key={"counter_name": counter_name},
            UpdateExpression="SET counter_value = if_not_exists(counter_value, :zero) + :inc",
            ExpressionAttributeValues={":inc": 1, ":zero": 0},
            ReturnValues="UPDATED_NEW",
        )
        seq = int(resp["Attributes"]["counter_value"])
    except Exception:
        return _gen_id(prefix)
    return f"{prefix}FY{fy}-{seq:04d}"

def _to_decimal(obj):
    if isinstance(obj, float): return Decimal(str(obj))
    if isinstance(obj, dict): return {k: _to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list): return [_to_decimal(i) for i in obj]
    return obj

def _from_decimal(obj):
    if isinstance(obj, Decimal): return float(obj)
    if isinstance(obj, dict): return {k: _from_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list): return [_from_decimal(i) for i in obj]
    return obj

def _scan_all(table_name):
    db = get_dynamodb()
    table = db.Table(table_name)
    resp = table.scan()
    items = resp.get("Items", [])
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return [_from_decimal(i) for i in items]


# ─── Cached Data Access ──────────────────────────────────────
# Uses @st.cache_data with TTL to avoid re-scanning DynamoDB on every render.
# Write operations call clear_cache() to invalidate stale data.

CACHE_TTL = 60  # seconds — data is reloaded from DynamoDB at most every 60s

# Per-run memory store: avoids even cache deserialization on repeated calls
# within the same Streamlit script run (which happens A LOT — pages call
# get_all_vendors() 2-3 times each). Cleared automatically on each new run.
_RUN_STORE = {}

def _get_from_run_store(key, loader):
    """Return data from per-run memory if available, else call loader and store."""
    if key not in _RUN_STORE:
        _RUN_STORE[key] = loader()
    return _RUN_STORE[key]

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _cached_scan(table_name):
    """Cached version of _scan_all. Keyed by table name."""
    return _scan_all(table_name)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _cached_query(table_name, key_name, key_value):
    """Cached DynamoDB query by partition key."""
    db = get_dynamodb()
    table = db.Table(table_name)
    resp = table.query(KeyConditionExpression=Key(key_name).eq(key_value))
    return [_from_decimal(i) for i in resp.get("Items", [])]


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _cached_get_item(table_name, key_dict_json):
    """Cached DynamoDB get_item. key_dict_json is JSON string of the key."""
    import json as _json
    key = _json.loads(key_dict_json)
    db = get_dynamodb()
    table = db.Table(table_name)
    resp = table.get_item(Key=key)
    item = resp.get("Item")
    return _from_decimal(item) if item else None


def clear_cache(table_name=None):
    """Clear ALL cached data. Call after any write operation."""
    global _RUN_STORE
    _RUN_STORE = {}
    try:
        _cached_scan.clear()
    except Exception:
        pass
    try:
        _cached_query.clear()
    except Exception:
        pass
    try:
        _cached_get_item.clear()
    except Exception:
        pass


def _invalidate_and_scan(table_name):
    """Clear cache for a table and return fresh data."""
    clear_cache()
    return _scan_all(table_name)


def _write_and_clear(func):
    """Decorator: clears the scan cache after any function that writes to DynamoDB."""
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        clear_cache()
        return result
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


def bulk_delete_all(table_name, key_fields):
    """Delete ALL items from a DynamoDB table. key_fields = list of key attribute names."""
    db = get_dynamodb()
    table = db.Table(table_name)
    items = _scan_all(table_name)
    deleted = 0
    with table.batch_writer() as batch:
        for item in items:
            key = {k: item[k] for k in key_fields if k in item}
            if key:
                # Convert back to proper types for DynamoDB keys
                for k, v in key.items():
                    if isinstance(v, float):
                        key[k] = str(v) if '.' not in str(v) else Decimal(str(v))
                batch.delete_item(Key=key)
                deleted += 1
    return deleted


def bulk_delete_table_data(table_key):
    """Delete all data from a table by its config key. Returns count deleted."""
    table_keys_map = {
        "master_items": ["item_id"],
        "projects": ["project_id"],
        "boq_items": ["project_id", "item_id"],
        "inventory": ["item_id"],
        "vendors": ["vendor_id"],
        "service_vendors": ["vendor_id"],
        "service_vendor_services": ["vendor_id", "service_id"],
        "raw_material_po": ["po_id"],
        "raw_material_po_items": ["po_id", "item_id"],
        "service_po": ["po_id"],
        "service_po_items": ["po_id", "item_id"],
        "production_tracking": ["project_id", "product_id"],
        "finished_goods": ["fg_id"],
        "dispatched_goods": ["dispatch_id"],
        "material_issues": ["issue_id"],
        "order_staging": ["stage_id"],
        "email_config": ["config_id"],
        "scrap_inventory": ["item_id"],
        "company_config": ["config_id"],
    }
    if table_key not in TABLES:
        return 0
    key_fields = table_keys_map.get(table_key, [])
    if not key_fields:
        return 0
    return bulk_delete_all(TABLES[table_key], key_fields)


def reset_counter(counter_name):
    """Reset a sequential counter to 0."""
    db = get_dynamodb()
    table = db.Table(COUNTER_TABLE)
    try:
        table.put_item(Item={"counter_name": counter_name, "counter_value": 0})
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  S3 — PDF Generation & Attachments
# ═══════════════════════════════════════════════════════════════════
@st.cache_data(ttl=300, show_spinner=False)
def _get_company_config():
    """Get company config (name, address, GSTIN, logo S3 key)."""
    try:
        db = get_dynamodb()
        table = db.Table(TABLES.get("company_config", "erp_company_config"))
        resp = table.get_item(Key={"config_id": "main"})
        item = resp.get("Item")
        if item:
            return _from_decimal(item)
    except Exception:
        pass
    return {
        "config_id": "main",
        "company_name": "YOUR COMPANY NAME",
        "address": "Your Address",
        "gstin": "XXXXXXXXXXXX",
        "logo_s3_key": "",
        "shipping_address": "Your Shipping Address",
    }


def save_company_config(config):
    db = get_dynamodb()
    table = db.Table(TABLES.get("company_config", "erp_company_config"))
    config["config_id"] = "main"
    config["updated_at"] = _now()
    table.put_item(Item=config)
    _get_company_config.clear()


def upload_company_logo(file_bytes, file_name):
    """Upload company logo to S3 and save the key."""
    s3_key = f"config/logo/{file_name}"
    try:
        s3 = get_s3_client()
        s3.put_object(Bucket=S3_ATTACHMENTS_BUCKET, Key=s3_key, Body=file_bytes, ContentType="image/png")
        cfg = _get_company_config()
        cfg["logo_s3_key"] = s3_key
        save_company_config(cfg)
        return s3_key
    except Exception as e:
        return None


def _get_logo_bytes():
    """Download logo from S3 for PDF embedding."""
    cfg = _get_company_config()
    key = cfg.get("logo_s3_key", "")
    if not key:
        return None
    try:
        s3 = get_s3_client()
        resp = s3.get_object(Bucket=S3_ATTACHMENTS_BUCKET, Key=key)
        return resp["Body"].read()
    except Exception:
        return None


def generate_po_pdf(po_data, po_items, po_type="Material"):
    """Generate a professional PO PDF matching the Airvent format."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT
    except ImportError:
        return None

    company = _get_company_config()
    vendor_name = po_data.get("vendor_name", "")
    # Try to get vendor details
    vendors = get_all_vendors() if po_type == "Material" else get_all_service_vendors()
    vendor_info = {}
    for v in vendors:
        if v.get("name", "").lower() == vendor_name.lower() or v.get("vendor_id") == po_data.get("vendor_id"):
            vendor_info = v
            break

    po_id = po_data.get("po_id", "")
    po_date = po_data.get("created_at", _today())[:10]
    delivery_date = po_data.get("expected_delivery", "")
    payment_terms = po_data.get("payment_terms", "")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=15*mm, bottomMargin=15*mm,
                             leftMargin=12*mm, rightMargin=12*mm)
    styles = getSampleStyleSheet()
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=7, leading=9)
    small_bold = ParagraphStyle("SmallBold", parent=small, fontName="Helvetica-Bold")
    header_style = ParagraphStyle("Header", parent=styles["Normal"], fontSize=8, leading=10)
    title_style = ParagraphStyle("POTitle", fontSize=16, fontName="Helvetica-Bold", alignment=TA_RIGHT)
    subtitle = ParagraphStyle("Subtitle", fontSize=11, fontName="Helvetica-Bold", alignment=TA_RIGHT)

    elements = []

    # ─── Header: Logo + PO Title ──────────────────────────────
    logo_bytes = _get_logo_bytes()
    header_data = []
    if logo_bytes:
        logo_buf = io.BytesIO(logo_bytes)
        try:
            logo_img = Image(logo_buf, width=50*mm, height=15*mm)
            logo_img.hAlign = "LEFT"
        except Exception:
            logo_img = Paragraph(f"<b>{company.get('company_name', '')}</b>", styles["Title"])
        header_data = [[logo_img, "", Paragraph("Purchase Order", title_style)],
                       ["", "", Paragraph(po_id, subtitle)]]
    else:
        header_data = [[Paragraph(f"<b>{company.get('company_name', '')}</b>", styles["Title"]),
                         "", Paragraph("Purchase Order", title_style)],
                       ["", "", Paragraph(po_id, subtitle)]]

    ht = Table(header_data, colWidths=[70*mm, 30*mm, 80*mm])
    ht.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                             ("LINEBELOW", (0, -1), (-1, -1), 1, colors.black)]))
    elements.append(ht)
    elements.append(Spacer(1, 4*mm))

    # ─── Address Block: Buyer | Supplier | Shipping ───────────
    buyer_text = f"""<b>Name and Address of Buyer</b><br/>
    <b>{company.get('company_name', '')}</b><br/>
    {company.get('address', '')}<br/>
    GSTIN: {company.get('gstin', '')}"""

    supplier_text = f"""<b>Name and Address of Supplier</b><br/>
    <b>{vendor_name}</b><br/>
    {vendor_info.get('address', '')}<br/>
    GSTIN: {vendor_info.get('gst_no', '')}"""

    shipping_text = f"""<b>Shipping Details</b><br/>
    {company.get('shipping_address', company.get('address', ''))}<br/>
    GSTIN: {company.get('gstin', '')}"""

    addr_table = Table([
        [Paragraph(buyer_text, small), Paragraph(supplier_text, small), Paragraph(shipping_text, small)]
    ], colWidths=[60*mm, 60*mm, 60*mm])
    addr_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(addr_table)
    elements.append(Spacer(1, 4*mm))

    # ─── PO Details Block ─────────────────────────────────────
    num_items = len(po_items)
    total_amount = sum(i.get("quantity", 0) * i.get("unit_price", i.get("rate", 0)) for i in po_items)
    gst_pct = po_data.get("gst_percentage", 18)
    gst_half = gst_pct / 2  # CGST and SGST each
    gst_amount = total_amount * gst_pct / 100
    cgst_amount = gst_amount / 2
    sgst_amount = gst_amount / 2
    grand_total = total_amount + gst_amount

    details_data = [
        [Paragraph("<b>PO Number</b>", small), Paragraph(po_id, small),
         Paragraph("<b>PO Date</b>", small), Paragraph(po_date, small)],
        [Paragraph("<b>Delivery Date</b>", small), Paragraph(str(delivery_date), small),
         Paragraph("<b>PO Amendment</b>", small), Paragraph("0", small)],
        [Paragraph("<b>No of Items</b>", small), Paragraph(str(num_items), small),
         Paragraph("<b>PO Amount</b>", small), Paragraph(f"INR {grand_total:,.2f}", small)],
        [Paragraph("<b>Payment Terms</b>", small), Paragraph(payment_terms, small), "", ""],
    ]
    dt = Table(details_data, colWidths=[35*mm, 55*mm, 35*mm, 55*mm])
    dt.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("SPAN", (0, 0), (0, 0)),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(Paragraph("<b>PO Details</b>", ParagraphStyle("PDT", fontSize=10, fontName="Helvetica-Bold", alignment=TA_CENTER)))
    elements.append(dt)
    elements.append(Spacer(1, 4*mm))

    # ─── Items Table ──────────────────────────────────────────
    col_widths = [8*mm, 50*mm, 20*mm, 20*mm, 22*mm, 18*mm, 18*mm, 22*mm]
    item_header = [
        Paragraph("<b>#</b>", small), Paragraph("<b>Description</b>", small),
        Paragraph("<b>HSN/SAC</b>", small), Paragraph("<b>Quantity</b>", small),
        Paragraph("<b>Rate</b>", small),
        Paragraph(f"<b>CGST ({gst_half:.0f}%)</b>", small),
        Paragraph(f"<b>SGST ({gst_half:.0f}%)</b>", small),
        Paragraph("<b>Total</b>", small),
    ]
    item_data = [item_header]

    for idx, item in enumerate(po_items, 1):
        qty = item.get("quantity", 0)
        rate = item.get("unit_price", item.get("rate", 0))
        taxable = qty * rate
        item_cgst = taxable * gst_half / 100
        item_sgst = taxable * gst_half / 100
        item_total = taxable + item_cgst + item_sgst
        desc_text = f"{item.get('description', '')}"
        if item.get("specification"):
            desc_text += f"<br/><font size='6'>Details: {item.get('specification', '')}</font>"
        item_data.append([
            Paragraph(str(idx), small),
            Paragraph(desc_text, small),
            Paragraph(item.get("hsn_code", ""), small),
            Paragraph(f"{qty} {item.get('unit', '')}", small),
            Paragraph(f"INR {rate:,.2f}", small),
            Paragraph(f"INR {item_cgst:,.2f}", small),
            Paragraph(f"INR {item_sgst:,.2f}", small),
            Paragraph(f"INR {item_total:,.2f}", small),
        ])

    # Total rows
    item_data.append(["", "", "", "", "",
        Paragraph("<b>Total (before Tax)</b>", small_bold), "",
        Paragraph(f"<b>INR {total_amount:,.2f}</b>", small_bold)])
    item_data.append(["", "", "", "", "",
        Paragraph(f"<b>CGST ({gst_half:.0f}%)</b>", small_bold), "",
        Paragraph(f"<b>INR {cgst_amount:,.2f}</b>", small_bold)])
    item_data.append(["", "", "", "", "",
        Paragraph(f"<b>SGST ({gst_half:.0f}%)</b>", small_bold), "",
        Paragraph(f"<b>INR {sgst_amount:,.2f}</b>", small_bold)])
    item_data.append(["", "", "", "", "",
        Paragraph(f"<b>Total Tax ({gst_pct:.0f}%)</b>", small_bold), "",
        Paragraph(f"<b>INR {gst_amount:,.2f}</b>", small_bold)])
    item_data.append(["", "", "", "", "",
        Paragraph("<b>Grand Total</b>", small_bold), "",
        Paragraph(f"<b>INR {grand_total:,.2f}</b>", small_bold)])

    it = Table(item_data, colWidths=col_widths, repeatRows=1)
    it.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
        ("BACKGROUND", (0, -5), (-1, -1), colors.HexColor("#f8f8f8")),
    ]))
    elements.append(it)
    elements.append(Spacer(1, 6*mm))

    # Notes
    if po_data.get("notes"):
        elements.append(Paragraph(f"<b>Notes:</b> {po_data['notes']}", small))
        elements.append(Spacer(1, 4*mm))

    # T&C + Signature
    elements.append(Paragraph("<b>Terms And Conditions:</b>", small_bold))
    elements.append(Paragraph("This is a computer generated document", small))
    elements.append(Spacer(1, 10*mm))
    sig_table = Table([["", Paragraph(f"For <b>{company.get('company_name', '')}</b><br/><br/><br/>Authorised Signatory",
                        ParagraphStyle("Sig", fontSize=8, alignment=TA_CENTER))]],
                       colWidths=[100*mm, 80*mm])
    elements.append(sig_table)

    doc.build(elements)
    buf.seek(0)

    s3_key = f"po/{po_type.lower()}/{_today()}/{po_id}.pdf"
    try:
        s3 = get_s3_client()
        s3.put_object(Bucket=S3_PO_PDF_BUCKET, Key=s3_key, Body=buf.getvalue(), ContentType="application/pdf")
        return s3_key
    except Exception as e:
        st.warning(f"S3 upload failed: {e}")
        return None


def generate_delivery_challan(po_data, po_items, challan_number=None):
    """Generate a Delivery Challan PDF for sending material to service vendor."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_RIGHT, TA_CENTER
    except ImportError:
        return None

    company = _get_company_config()
    po_id = po_data.get("po_id", "")
    if not challan_number:
        challan_number = f"DC-{po_id}"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=15*mm, bottomMargin=15*mm,
                             leftMargin=12*mm, rightMargin=12*mm)
    styles = getSampleStyleSheet()
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, leading=10)
    small_bold = ParagraphStyle("SmallBold", parent=small, fontName="Helvetica-Bold")

    elements = []

    # Header
    logo_bytes = _get_logo_bytes()
    title_style = ParagraphStyle("DCTitle", fontSize=16, fontName="Helvetica-Bold", alignment=TA_RIGHT)
    if logo_bytes:
        logo_buf = io.BytesIO(logo_bytes)
        try:
            logo_img = Image(logo_buf, width=50*mm, height=15*mm)
        except Exception:
            logo_img = Paragraph(f"<b>{company.get('company_name', '')}</b>", styles["Title"])
        ht = Table([[logo_img, Paragraph("Delivery Challan", title_style)]], colWidths=[90*mm, 90*mm])
    else:
        ht = Table([[Paragraph(f"<b>{company.get('company_name', '')}</b>", styles["Title"]),
                      Paragraph("Delivery Challan", title_style)]], colWidths=[90*mm, 90*mm])
    ht.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                             ("LINEBELOW", (0, -1), (-1, -1), 1, colors.black)]))
    elements.append(ht)
    elements.append(Spacer(1, 4*mm))

    # Details
    details = [
        [Paragraph("<b>Challan No:</b>", small), Paragraph(challan_number, small),
         Paragraph("<b>Date:</b>", small), Paragraph(_today(), small)],
        [Paragraph("<b>PO Reference:</b>", small), Paragraph(po_id, small),
         Paragraph("<b>Vendor:</b>", small), Paragraph(po_data.get("vendor_name", ""), small)],
        [Paragraph("<b>From:</b>", small), Paragraph(company.get("address", ""), small),
         Paragraph("<b>To:</b>", small), Paragraph(po_data.get("vendor_address", ""), small)],
    ]
    dt = Table(details, colWidths=[25*mm, 65*mm, 25*mm, 65*mm])
    dt.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                             ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                             ("TOPPADDING", (0, 0), (-1, -1), 3),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    elements.append(dt)
    elements.append(Spacer(1, 4*mm))

    # Items
    item_header = [Paragraph("<b>Sl No</b>", small_bold), Paragraph("<b>Item Name</b>", small_bold),
                    Paragraph("<b>HSN Code</b>", small_bold), Paragraph("<b>Quantity</b>", small_bold),
                    Paragraph("<b>Unit</b>", small_bold)]
    item_data = [item_header]
    for idx, item in enumerate(po_items, 1):
        item_data.append([
            Paragraph(str(idx), small),
            Paragraph(item.get("description", item.get("item_name", "")), small),
            Paragraph(item.get("hsn_code", ""), small),
            Paragraph(str(item.get("quantity", 0)), small),
            Paragraph(item.get("unit", ""), small),
        ])

    it = Table(item_data, colWidths=[15*mm, 80*mm, 25*mm, 25*mm, 25*mm], repeatRows=1)
    it.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(it)
    elements.append(Spacer(1, 10*mm))

    # Signatures
    sig = Table([
        [Paragraph("Prepared By: ________________", small),
         Paragraph("Received By: ________________", small)],
        [Paragraph(f"\n\nFor <b>{company.get('company_name', '')}</b>", small), ""],
    ], colWidths=[90*mm, 90*mm])
    elements.append(sig)

    doc.build(elements)
    buf.seek(0)

    s3_key = f"challans/{_today()}/{challan_number}.pdf"
    try:
        s3 = get_s3_client()
        s3.put_object(Bucket=S3_PO_PDF_BUCKET, Key=s3_key, Body=buf.getvalue(), ContentType="application/pdf")
        return s3_key
    except Exception:
        return None


def get_po_pdf_download(s3_key):
    """Download PDF bytes from S3 for display."""
    try:
        s3 = get_s3_client()
        resp = s3.get_object(Bucket=S3_PO_PDF_BUCKET, Key=s3_key)
        return resp["Body"].read()
    except Exception:
        return None


def upload_attachment(po_id, file_name, file_bytes, content_type="application/octet-stream"):
    """Upload an attachment to persistent S3 bucket. Returns S3 key."""
    s3_key = f"attachments/{po_id}/{file_name}"
    try:
        s3 = get_s3_client()
        s3.put_object(Bucket=S3_ATTACHMENTS_BUCKET, Key=s3_key, Body=file_bytes,
                       ContentType=content_type)
        return s3_key
    except Exception as e:
        st.warning(f"Attachment upload failed: {e}")
        return None


def get_attachment(s3_key):
    """Download attachment bytes from S3."""
    try:
        s3 = get_s3_client()
        resp = s3.get_object(Bucket=S3_ATTACHMENTS_BUCKET, Key=s3_key)
        return resp["Body"].read()
    except Exception:
        return None


def list_attachments(po_id):
    """List all attachments for a PO."""
    try:
        s3 = get_s3_client()
        resp = s3.list_objects_v2(Bucket=S3_ATTACHMENTS_BUCKET, Prefix=f"attachments/{po_id}/")
        return [obj["Key"] for obj in resp.get("Contents", [])]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════
#  EMAIL — SES Configuration & Sending
# ═══════════════════════════════════════════════════════════════════
EMAIL_CONFIG_TABLE = "erp_email_config"

@st.cache_resource
def get_ses_client():
    return boto3.client("ses", **_get_aws_config())


@st.cache_data(ttl=300, show_spinner=False)
def get_email_config():
    """Get email config from DynamoDB. Returns dict or defaults."""
    try:
        db = get_dynamodb()
        table = db.Table(EMAIL_CONFIG_TABLE)
        resp = table.get_item(Key={"config_id": "main"})
        item = resp.get("Item")
        if item:
            return _from_decimal(item)
    except Exception:
        pass
    return {
        "config_id": "main",
        "sender_email": st.secrets.get("aws", {}).get("SES_SENDER_EMAIL", "erp@yourdomain.com"),
        "management_emails": [],
        "reminder_enabled": True,
        "digest_enabled": True,
        "company_name": "FabriFlow",
    }


def save_email_config(config):
    """Save email config to DynamoDB."""
    try:
        db = get_dynamodb()
        table = db.Table(EMAIL_CONFIG_TABLE)
        config["config_id"] = "main"
        config["updated_at"] = _now()
        table.put_item(Item=config)
        get_email_config.clear()
        return True
    except Exception as e:
        st.error(f"Failed to save config: {e}")
        return False


def send_email(to_addresses, subject, html_body, sender_email=None):
    """Send an email via SES. Returns True on success."""
    if not sender_email:
        cfg = get_email_config()
        sender_email = cfg.get("sender_email", "erp@yourdomain.com")
    if isinstance(to_addresses, str):
        to_addresses = [to_addresses]
    try:
        ses = get_ses_client()
        ses.send_email(
            Source=sender_email,
            Destination={"ToAddresses": to_addresses},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}},
            },
        )
        return True
    except Exception as e:
        return str(e)


def send_test_email(to_address, sender_email=None):
    """Send a test email."""
    html = """
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden">
        <div style="background:#1e40af;color:white;padding:20px;text-align:center">
            <h2 style="margin:0">🏭 FabriFlow ERP — Test Email</h2>
        </div>
        <div style="padding:24px">
            <p>This is a <strong>test email</strong> from your FabriFlow ERP system.</p>
            <p>If you received this, your SES email configuration is working correctly.</p>
            <hr style="border-color:#e2e8f0">
            <p style="color:#64748b;font-size:0.85rem">Sent at: {time}</p>
        </div>
    </div>
    """.format(time=_now())
    return send_email(to_address, "FabriFlow ERP — Test Email", html, sender_email)


def send_po_email(po_data, po_items, vendor_email, po_type="Material"):
    """Send PO notification email to vendor."""
    cfg = get_email_config()
    sender = cfg.get("sender_email", "erp@yourdomain.com")
    company = cfg.get("company_name", "FabriFlow")
    po_id = po_data.get("po_id", "")

    rows = ""
    for i, item in enumerate(po_items, 1):
        qty = item.get("quantity", 0)
        rate = item.get("unit_price", item.get("rate", 0))
        rows += f"""<tr>
            <td style="padding:8px;border:1px solid #e2e8f0">{i}</td>
            <td style="padding:8px;border:1px solid #e2e8f0">{item.get('description', '')}</td>
            <td style="padding:8px;border:1px solid #e2e8f0">{item.get('specification', '')}</td>
            <td style="padding:8px;border:1px solid #e2e8f0;text-align:right">{qty} {item.get('unit', '')}</td>
            <td style="padding:8px;border:1px solid #e2e8f0;text-align:right">₹{rate:,.2f}</td>
            <td style="padding:8px;border:1px solid #e2e8f0;text-align:right">₹{qty * rate:,.2f}</td>
        </tr>"""

    total = sum(i.get("quantity", 0) * i.get("unit_price", i.get("rate", 0)) for i in po_items)

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:700px;margin:auto;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden">
        <div style="background:#1e40af;color:white;padding:20px">
            <h2 style="margin:0">{po_type} Purchase Order: {po_id}</h2>
            <p style="margin:4px 0 0 0;opacity:0.9">From {company}</p>
        </div>
        <div style="padding:24px">
            <p>Dear <strong>{po_data.get('vendor_name', '')}</strong>,</p>
            <p>Please find below the purchase order details:</p>
            <table style="width:100%;border-collapse:collapse;margin:16px 0">
                <tr style="background:#f1f5f9">
                    <th style="padding:8px;border:1px solid #e2e8f0">#</th>
                    <th style="padding:8px;border:1px solid #e2e8f0">Description</th>
                    <th style="padding:8px;border:1px solid #e2e8f0">Specification</th>
                    <th style="padding:8px;border:1px solid #e2e8f0">Qty</th>
                    <th style="padding:8px;border:1px solid #e2e8f0">Rate</th>
                    <th style="padding:8px;border:1px solid #e2e8f0">Amount</th>
                </tr>
                {rows}
                <tr style="background:#f1f5f9;font-weight:bold">
                    <td colspan="5" style="padding:8px;border:1px solid #e2e8f0;text-align:right">Total</td>
                    <td style="padding:8px;border:1px solid #e2e8f0;text-align:right">₹{total:,.2f}</td>
                </tr>
            </table>
            <p><strong>Payment Terms:</strong> {po_data.get('payment_terms', '')}</p>
            <p><strong>Expected Delivery:</strong> {po_data.get('expected_delivery', '')}</p>
            {f"<p><strong>Notes:</strong> {po_data.get('notes', '')}</p>" if po_data.get('notes') else ''}
            <p>Please acknowledge receipt of this PO.</p>
            <hr style="border-color:#e2e8f0">
            <p style="color:#64748b;font-size:0.8rem">This is an automated email from {company} ERP.</p>
        </div>
    </div>"""

    return send_email(vendor_email, f"{po_type} PO: {po_id} — {company}", html, sender)


def send_reminder_email(po_data, vendor_email, management_emails, po_type="Material"):
    """Send delivery reminder to vendor and/or management."""
    cfg = get_email_config()
    sender = cfg.get("sender_email", "")
    company = cfg.get("company_name", "FabriFlow")
    po_id = po_data.get("po_id", "")

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;border:1px solid #fbbf24;border-radius:8px;overflow:hidden">
        <div style="background:#d97706;color:white;padding:20px">
            <h2 style="margin:0">⚠️ Delivery Reminder — {po_id}</h2>
        </div>
        <div style="padding:24px">
            <p>This is a reminder that the following {po_type} PO is <strong>due for delivery tomorrow</strong>:</p>
            <table style="margin:16px 0">
                <tr><td style="padding:4px 12px;font-weight:bold">PO Number:</td><td>{po_id}</td></tr>
                <tr><td style="padding:4px 12px;font-weight:bold">Vendor:</td><td>{po_data.get('vendor_name', '')}</td></tr>
                <tr><td style="padding:4px 12px;font-weight:bold">Expected Delivery:</td><td>{po_data.get('expected_delivery', '')}</td></tr>
                <tr><td style="padding:4px 12px;font-weight:bold">Amount:</td><td>₹{po_data.get('total_amount', 0):,.2f}</td></tr>
            </table>
            <p>Please ensure timely delivery.</p>
            <p style="color:#64748b;font-size:0.8rem">— {company} ERP</p>
        </div>
    </div>"""

    results = []
    if vendor_email:
        results.append(("Vendor", send_email(vendor_email, f"Delivery Reminder: {po_id}", html, sender)))
    for mgmt in (management_emails or []):
        if mgmt:
            results.append(("Management", send_email(mgmt, f"Delivery Reminder: {po_id}", html, sender)))
    return results


def send_weekly_digest(pos_received, management_emails):
    """Send weekly digest of received POs."""
    cfg = get_email_config()
    sender = cfg.get("sender_email", "")
    company = cfg.get("company_name", "FabriFlow")

    rows = ""
    total_value = 0
    for po in pos_received:
        amt = po.get("total_amount", 0)
        total_value += amt
        rows += f"""<tr>
            <td style="padding:8px;border:1px solid #e2e8f0">{po.get('po_id', '')}</td>
            <td style="padding:8px;border:1px solid #e2e8f0">{po.get('vendor_name', '')}</td>
            <td style="padding:8px;border:1px solid #e2e8f0">{po.get('status', '')}</td>
            <td style="padding:8px;border:1px solid #e2e8f0;text-align:right">₹{amt:,.2f}</td>
            <td style="padding:8px;border:1px solid #e2e8f0">{po.get('expected_delivery', '')}</td>
        </tr>"""

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:700px;margin:auto;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden">
        <div style="background:#16a34a;color:white;padding:20px">
            <h2 style="margin:0">📊 Weekly PO Digest — {company}</h2>
            <p style="margin:4px 0 0 0;opacity:0.9">Week ending {_today()}</p>
        </div>
        <div style="padding:24px">
            <p><strong>{len(pos_received)}</strong> POs received/completed this week, totaling <strong>₹{total_value:,.2f}</strong></p>
            <table style="width:100%;border-collapse:collapse;margin:16px 0">
                <tr style="background:#f1f5f9">
                    <th style="padding:8px;border:1px solid #e2e8f0">PO #</th>
                    <th style="padding:8px;border:1px solid #e2e8f0">Vendor</th>
                    <th style="padding:8px;border:1px solid #e2e8f0">Status</th>
                    <th style="padding:8px;border:1px solid #e2e8f0">Amount</th>
                    <th style="padding:8px;border:1px solid #e2e8f0">Delivery</th>
                </tr>
                {rows if rows else '<tr><td colspan="5" style="padding:16px;text-align:center;color:#94a3b8">No POs received this week</td></tr>'}
            </table>
            <p style="color:#64748b;font-size:0.8rem">— {company} ERP automated digest</p>
        </div>
    </div>"""

    results = []
    for mgmt in (management_emails or []):
        if mgmt:
            results.append(send_email(mgmt, f"Weekly PO Digest — {company} — {_today()}", html, sender))
    return results


# ═══════════════════════════════════════════════════════════════════
#  MASTER ITEMS
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def add_master_item(item_name, vendor, category, sub_category, specification, unit, location, price, revised_price=0, remarks=""):
    db = get_dynamodb()
    table = db.Table(TABLES["master_items"])
    item = _to_decimal({
        "item_id": _next_sequential_id("MI-"), "item_name": item_name, "vendor": vendor,
        "category": category, "sub_category": sub_category, "specification": specification,
        "unit": unit, "location": location, "price": price, "revised_price": revised_price,
        "remarks": remarks, "created_at": _now(), "updated_at": _now(),
    })
    table.put_item(Item=item)
    return _from_decimal(item)

def get_all_master_items():
    return _get_from_run_store("master_items", lambda: _cached_scan(TABLES["master_items"]))

def get_master_item(item_id):
    return _cached_get_item(TABLES["master_items"], json.dumps({"item_id": item_id}))

@_write_and_clear
def update_master_item(item_id, updates):
    db = get_dynamodb()
    table = db.Table(TABLES["master_items"])
    expr_parts, expr_values, expr_names = [], {}, {}
    for k, v in updates.items():
        safe_key = f"#k_{k}"
        expr_names[safe_key] = k
        expr_parts.append(f"{safe_key} = :{k}")
        expr_values[f":{k}"] = _to_decimal(v) if isinstance(v, float) else v
    expr_parts.append("#k_updated_at = :updated_at")
    expr_names["#k_updated_at"] = "updated_at"
    expr_values[":updated_at"] = _now()
    table.update_item(Key={"item_id": item_id},
        UpdateExpression="SET " + ", ".join(expr_parts),
        ExpressionAttributeNames=expr_names, ExpressionAttributeValues=expr_values)

@_write_and_clear
def delete_master_item(item_id):
    db = get_dynamodb()
    table = db.Table(TABLES["master_items"])
    table.delete_item(Key={"item_id": item_id})

def get_master_items_by_vendor(vendor_name):
    items = get_all_master_items()
    return [i for i in items if i.get("vendor", "").lower() == vendor_name.lower()]

@_write_and_clear
def bulk_upload_master_items(items_list):
    """Bulk upload master items. Auto-creates vendors (deduplicated)."""
    # Collect unique vendor names and create them first
    vendor_names = set(str(item.get("vendor", "")).strip() for item in items_list if item.get("vendor", "").strip())
    for vname in vendor_names:
        ensure_vendor_exists(vname)
    # Now upload items
    results = []
    for item in items_list:
        r = add_master_item(item.get("item_name", ""), str(item.get("vendor", "")).strip(),
            item.get("category", ""), item.get("sub_category", ""),
            item.get("specification", ""), item.get("unit", "Nos"),
            item.get("location", "Main Store"), float(item.get("price", 0)),
            float(item.get("revised_price", 0)), item.get("remarks", ""))
        results.append(r)
    return results


# ═══════════════════════════════════════════════════════════════════
#  PROJECTS
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def create_project(name, client_name, description, product_type, status="Planning"):
    db = get_dynamodb()
    table = db.Table(TABLES["projects"])
    item = {"project_id": _gen_id("PRJ-"), "name": name, "client_name": client_name,
            "description": description, "product_type": product_type, "status": status,
            "created_at": _now(), "updated_at": _now()}
    table.put_item(Item=item)
    return item

def get_all_projects():
    return _get_from_run_store("projects", lambda: _cached_scan(TABLES["projects"]))

def get_project(project_id):
    return _cached_get_item(TABLES["projects"], json.dumps({"project_id": project_id}))

@_write_and_clear
def update_project_status(project_id, status):
    db = get_dynamodb()
    table = db.Table(TABLES["projects"])
    table.update_item(Key={"project_id": project_id},
        UpdateExpression="SET #s = :s, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":u": _now()})


# ═══════════════════════════════════════════════════════════════════
#  BOQ ITEMS
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def add_boq_item(project_id, master_item_id, item_name, vendor, category, sub_category,
                 specification, quantity, unit, rate):
    db = get_dynamodb()
    table = db.Table(TABLES["boq_items"])
    item = _to_decimal({
        "project_id": project_id, "item_id": _gen_id("BOQ-"),
        "master_item_id": master_item_id, "item_name": item_name, "vendor": vendor,
        "category": category, "sub_category": sub_category,
        "specification": specification, "quantity": quantity, "unit": unit,
        "rate": rate, "total": quantity * rate, "staged": False, "created_at": _now(),
    })
    table.put_item(Item=item)
    return _from_decimal(item)

def get_boq_items(project_id):
    return _get_from_run_store(f"boq_{project_id}", lambda: _cached_query(TABLES["boq_items"], "project_id", project_id))

def get_unstaged_boq_items(project_id):
    """Get only BOQ items that haven't been staged yet."""
    all_items = get_boq_items(project_id)
    return [i for i in all_items if not i.get("staged", False)]

@_write_and_clear
def mark_boq_items_staged(project_id, item_ids):
    """Mark specific BOQ items as staged."""
    db = get_dynamodb()
    table = db.Table(TABLES["boq_items"])
    for item_id in item_ids:
        table.update_item(
            Key={"project_id": project_id, "item_id": item_id},
            UpdateExpression="SET staged = :s",
            ExpressionAttributeValues={":s": True},
        )

@_write_and_clear
def delete_boq_item(project_id, item_id):
    db = get_dynamodb()
    table = db.Table(TABLES["boq_items"])
    table.delete_item(Key={"project_id": project_id, "item_id": item_id})

@_write_and_clear
def update_boq_item(project_id, item_id, quantity, rate):
    db = get_dynamodb()
    table = db.Table(TABLES["boq_items"])
    table.update_item(Key={"project_id": project_id, "item_id": item_id},
        UpdateExpression="SET quantity = :q, rate = :r, #t = :t",
        ExpressionAttributeNames={"#t": "total"},
        ExpressionAttributeValues={":q": Decimal(str(quantity)), ":r": Decimal(str(rate)),
                                   ":t": Decimal(str(quantity * rate))})


# ═══════════════════════════════════════════════════════════════════
#  VENDORS
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def add_vendor(name, contact_person="—", phone="—", email="—", address="—", gst_no="—", payment_terms="Credit - 30 Days"):
    db = get_dynamodb()
    table = db.Table(TABLES["vendors"])
    item = {"vendor_id": _gen_id("VEN-"), "name": name, "contact_person": contact_person,
            "phone": phone, "email": email, "address": address, "gst_no": gst_no,
            "payment_terms": payment_terms, "created_at": _now()}
    table.put_item(Item=item)
    return item

def get_all_vendors():
    return _get_from_run_store("vendors", lambda: _cached_scan(TABLES["vendors"]))

def get_vendor(vendor_id):
    return _cached_get_item(TABLES["vendors"], json.dumps({"vendor_id": vendor_id}))

@_write_and_clear
def update_vendor(vendor_id, updates):
    db = get_dynamodb()
    table = db.Table(TABLES["vendors"])
    expr_parts, expr_values, expr_names = [], {}, {}
    for k, v in updates.items():
        safe = f"#v_{k}"
        expr_names[safe] = k
        expr_parts.append(f"{safe} = :{k}")
        expr_values[f":{k}"] = v
    table.update_item(Key={"vendor_id": vendor_id},
        UpdateExpression="SET " + ", ".join(expr_parts),
        ExpressionAttributeNames=expr_names, ExpressionAttributeValues=expr_values)

@_write_and_clear
def ensure_vendor_exists(vendor_name):
    """Auto-create vendor if it doesn't exist. Returns vendor record."""
    vendors = get_all_vendors()
    for v in vendors:
        if v.get("name", "").lower().strip() == vendor_name.lower().strip():
            return v
    return add_vendor(vendor_name)

@_write_and_clear
def delete_vendor(vendor_id):
    db = get_dynamodb()
    table = db.Table(TABLES["vendors"])
    table.delete_item(Key={"vendor_id": vendor_id})


# ═══════════════════════════════════════════════════════════════════
#  SERVICE VENDORS
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def add_service_vendor(name, contact_person, phone, email, address, gst_no, payment_terms):
    db = get_dynamodb()
    table = db.Table(TABLES["service_vendors"])
    item = {"vendor_id": _gen_id("SV-"), "name": name, "contact_person": contact_person,
            "phone": phone, "email": email, "address": address, "gst_no": gst_no,
            "payment_terms": payment_terms, "created_at": _now()}
    table.put_item(Item=item)
    return item

def get_all_service_vendors():
    return _get_from_run_store("service_vendors", lambda: _cached_scan(TABLES["service_vendors"]))

def get_service_vendor(vendor_id):
    return _cached_get_item(TABLES["service_vendors"], json.dumps({"vendor_id": vendor_id}))

@_write_and_clear
def update_service_vendor(vendor_id, updates):
    db = get_dynamodb()
    table = db.Table(TABLES["service_vendors"])
    expr_parts, expr_values, expr_names = [], {}, {}
    for k, v in updates.items():
        safe = f"#sv_{k}"
        expr_names[safe] = k
        expr_parts.append(f"{safe} = :{k}")
        expr_values[f":{k}"] = v
    table.update_item(Key={"vendor_id": vendor_id},
        UpdateExpression="SET " + ", ".join(expr_parts),
        ExpressionAttributeNames=expr_names, ExpressionAttributeValues=expr_values)

@_write_and_clear
def delete_service_vendor(vendor_id):
    db = get_dynamodb()
    table = db.Table(TABLES["service_vendors"])
    table.delete_item(Key={"vendor_id": vendor_id})

@_write_and_clear
def add_service_vendor_service(vendor_id, service_name, description, unit, rate):
    db = get_dynamodb()
    table = db.Table(TABLES["service_vendor_services"])
    item = _to_decimal({"vendor_id": vendor_id, "service_id": _gen_id("SVC-"),
            "service_name": service_name, "description": description,
            "unit": unit, "rate": rate, "updated_at": _now()})
    table.put_item(Item=item)
    return _from_decimal(item)

def get_service_vendor_services(vendor_id):
    return _cached_query(TABLES["service_vendor_services"], "vendor_id", vendor_id)


# ═══════════════════════════════════════════════════════════════════
#  ORDER STAGING — fixed reserved keyword 'items'
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def create_staged_orders_from_boq(project_id):
    """Stage only NEW (unstaged) BOQ items. Already-staged items are skipped."""
    unstaged = get_unstaged_boq_items(project_id)
    if not unstaged:
        return []
    db = get_dynamodb()
    table = db.Table(TABLES["order_staging"])
    vendor_groups = {}
    for item in unstaged:
        vendor = item.get("vendor", "Unknown")
        if vendor not in vendor_groups:
            vendor_groups[vendor] = []
        vendor_groups[vendor].append(item)
    staged = []
    staged_item_ids = []
    for vendor_name, vitems in vendor_groups.items():
        stage_id = _gen_id("STG-")
        total = sum(i.get("total", 0) for i in vitems)
        entry = _to_decimal({
            "stage_id": stage_id, "project_id": project_id,
            "vendor_name": vendor_name, "line_items": vitems,
            "total_amount": total, "status": "Staged",
            "created_at": _now(), "updated_at": _now(),
        })
        table.put_item(Item=entry)
        staged.append(_from_decimal(entry))
        staged_item_ids.extend([i["item_id"] for i in vitems])
    # Mark all these BOQ items as staged
    if staged_item_ids:
        mark_boq_items_staged(project_id, staged_item_ids)
    return staged

def get_staged_orders(project_id=None):
    db = get_dynamodb()
    table = db.Table(TABLES["order_staging"])
    if project_id:
        resp = table.scan(FilterExpression=Attr("project_id").eq(project_id))
    else:
        resp = table.scan()
    return [_from_decimal(i) for i in resp.get("Items", [])]

def get_staged_order(stage_id):
    db = get_dynamodb()
    table = db.Table(TABLES["order_staging"])
    resp = table.get_item(Key={"stage_id": stage_id})
    item = resp.get("Item")
    return _from_decimal(item) if item else None

@_write_and_clear
def update_staged_order_items(stage_id, line_items, total_amount):
    db = get_dynamodb()
    table = db.Table(TABLES["order_staging"])
    table.update_item(Key={"stage_id": stage_id},
        UpdateExpression="SET #li = :i, total_amount = :t, updated_at = :u",
        ExpressionAttributeNames={"#li": "line_items"},
        ExpressionAttributeValues={":i": _to_decimal(line_items),
                                   ":t": Decimal(str(total_amount)), ":u": _now()})

@_write_and_clear
def update_staged_order_status(stage_id, status):
    db = get_dynamodb()
    table = db.Table(TABLES["order_staging"])
    table.update_item(Key={"stage_id": stage_id},
        UpdateExpression="SET #s = :s, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":u": _now()})

@_write_and_clear
def delete_staged_order(stage_id):
    db = get_dynamodb()
    table = db.Table(TABLES["order_staging"])
    table.delete_item(Key={"stage_id": stage_id})


# ═══════════════════════════════════════════════════════════════════
#  RAW MATERIAL PURCHASE ORDERS
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def create_raw_material_po(project_id, vendor_id, vendor_name, payment_terms, expected_delivery, items, notes="", gst_percentage=18):
    db = get_dynamodb()
    po_table = db.Table(TABLES["raw_material_po"])
    items_table = db.Table(TABLES["raw_material_po_items"])
    po_id = _next_po_id("RMPO-")
    total_amount = sum(i["quantity"] * i["unit_price"] for i in items)
    po = _to_decimal({"po_id": po_id, "project_id": project_id, "vendor_id": vendor_id,
            "vendor_name": vendor_name, "payment_terms": payment_terms,
            "expected_delivery": expected_delivery, "total_amount": total_amount,
            "gst_percentage": gst_percentage,
            "status": "Draft", "notes": notes, "email_sent": False,
            "pdf_key": "", "created_at": _now(), "updated_at": _now()})
    po_table.put_item(Item=po)
    for item in items:
        po_item = _to_decimal({"po_id": po_id, "item_id": _gen_id("POI-"),
                "description": item["description"], "specification": item.get("specification", ""),
                "quantity": item["quantity"], "unit": item["unit"],
                "unit_price": item["unit_price"], "total_price": item["quantity"] * item["unit_price"],
                "quantity_received": 0, "received": False})
        items_table.put_item(Item=po_item)
    return _from_decimal(po)

def get_all_raw_material_pos(project_id=None):
    if project_id:
        db = get_dynamodb()
        table = db.Table(TABLES["raw_material_po"])
        resp = table.scan(FilterExpression=Attr("project_id").eq(project_id))
        return [_from_decimal(i) for i in resp.get("Items", [])]
    return _get_from_run_store("rm_pos", lambda: _cached_scan(TABLES["raw_material_po"]))

def get_raw_material_po(po_id):
    return _cached_get_item(TABLES["raw_material_po"], json.dumps({"po_id": po_id}))

def get_raw_material_po_items(po_id):
    return _cached_query(TABLES["raw_material_po_items"], "po_id", po_id)

@_write_and_clear
def update_po_item_receipt(po_id, item_id, quantity_received, received=False):
    db = get_dynamodb()
    table = db.Table(TABLES["raw_material_po_items"])
    table.update_item(Key={"po_id": po_id, "item_id": item_id},
        UpdateExpression="SET quantity_received = :qr, received = :r",
        ExpressionAttributeValues={":qr": Decimal(str(quantity_received)), ":r": received})

@_write_and_clear
def update_raw_material_po_status(po_id, status):
    db = get_dynamodb()
    table = db.Table(TABLES["raw_material_po"])
    table.update_item(Key={"po_id": po_id},
        UpdateExpression="SET #s = :s, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":u": _now()})

def update_po_pdf_key(po_id, pdf_key, table_key="raw_material_po"):
    db = get_dynamodb()
    table = db.Table(TABLES[table_key])
    table.update_item(Key={"po_id": po_id},
        UpdateExpression="SET pdf_key = :pk",
        ExpressionAttributeValues={":pk": pdf_key})

@_write_and_clear
def delete_raw_material_po(po_id):
    """Delete a draft PO and all its line items."""
    db = get_dynamodb()
    # Delete line items
    items_table = db.Table(TABLES["raw_material_po_items"])
    items = get_raw_material_po_items(po_id)
    for item in items:
        items_table.delete_item(Key={"po_id": po_id, "item_id": item["item_id"]})
    # Delete PO
    po_table = db.Table(TABLES["raw_material_po"])
    po_table.delete_item(Key={"po_id": po_id})

@_write_and_clear
def delete_service_po(po_id):
    """Delete a draft Service PO and all its line items."""
    db = get_dynamodb()
    items_table = db.Table(TABLES["service_po_items"])
    items = get_service_po_items(po_id)
    for item in items:
        items_table.delete_item(Key={"po_id": po_id, "item_id": item["item_id"]})
    po_table = db.Table(TABLES["service_po"])
    po_table.delete_item(Key={"po_id": po_id})

@_write_and_clear
def place_po_via_sqs(po_id, vendor_email, vendor_name, items, total_amount, payment_terms, expected_delivery):
    """Place PO: update status + send email to vendor + notify management."""
    update_raw_material_po_status(po_id, "Placed")
    po_data = get_raw_material_po(po_id)
    po_items = get_raw_material_po_items(po_id)
    # Email vendor
    if vendor_email:
        send_po_email(po_data or {}, po_items or items, vendor_email, "Material")
    # Email management
    cfg = get_email_config()
    mgmt = cfg.get("management_emails", [])
    sender = cfg.get("sender_email", "")
    company = cfg.get("company_name", "FabriFlow")
    if mgmt and sender:
        html = f"""<div style="font-family:Arial,sans-serif;padding:20px">
            <h3>📦 Material PO Placed: {po_id}</h3>
            <p><strong>Vendor:</strong> {vendor_name}</p>
            <p><strong>Amount:</strong> ₹{total_amount:,.2f}</p>
            <p><strong>Payment:</strong> {payment_terms}</p>
            <p><strong>Expected Delivery:</strong> {expected_delivery}</p>
            <p style="color:#64748b;font-size:0.85rem">— {company} ERP</p>
        </div>"""
        for m in mgmt:
            if m:
                send_email(m, f"PO Placed: {po_id} — {vendor_name}", html, sender)
    return True


# ═══════════════════════════════════════════════════════════════════
#  SERVICE PURCHASE ORDERS
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def create_service_po(project_id, vendor_id, vendor_name, payment_terms, expected_delivery, services, notes="", gst_percentage=18):
    db = get_dynamodb()
    po_table = db.Table(TABLES["service_po"])
    items_table = db.Table(TABLES["service_po_items"])
    po_id = _next_po_id("SPO-")
    total_amount = sum(s["quantity"] * s["unit_price"] for s in services)
    po = _to_decimal({"po_id": po_id, "project_id": project_id, "vendor_id": vendor_id,
            "vendor_name": vendor_name, "payment_terms": payment_terms,
            "expected_delivery": expected_delivery, "total_amount": total_amount,
            "gst_percentage": gst_percentage,
            "status": "Draft", "notes": notes, "email_sent": False,
            "pdf_key": "", "created_at": _now(), "updated_at": _now()})
    po_table.put_item(Item=po)
    for svc in services:
        svc_item = _to_decimal({"po_id": po_id, "item_id": _gen_id("SPI-"),
                "description": svc["description"], "specification": svc.get("specification", ""),
                "quantity": svc["quantity"], "unit": svc["unit"],
                "unit_price": svc["unit_price"], "total_price": svc["quantity"] * svc["unit_price"],
                "quantity_received": 0, "received": False,
                "finishing_status": "Pending", "finishing_comment": "",
                "scrap_received": 0, "scrap_usable": False, "scrap_notes": ""})
        items_table.put_item(Item=svc_item)
    return _from_decimal(po)

def get_all_service_pos(project_id=None):
    if project_id:
        db = get_dynamodb()
        table = db.Table(TABLES["service_po"])
        resp = table.scan(FilterExpression=Attr("project_id").eq(project_id))
        return [_from_decimal(i) for i in resp.get("Items", [])]
    return _get_from_run_store("svc_pos", lambda: _cached_scan(TABLES["service_po"]))

def get_service_po_items(po_id):
    return _cached_query(TABLES["service_po_items"], "po_id", po_id)

@_write_and_clear
def update_service_po_item(po_id, item_id, quantity_received, received, finishing_status, finishing_comment, scrap_received, scrap_usable, scrap_notes):
    db = get_dynamodb()
    table = db.Table(TABLES["service_po_items"])
    table.update_item(Key={"po_id": po_id, "item_id": item_id},
        UpdateExpression="SET quantity_received=:qr, received=:r, finishing_status=:fs, finishing_comment=:fc, scrap_received=:sr, scrap_usable=:su, scrap_notes=:sn",
        ExpressionAttributeValues={":qr": Decimal(str(quantity_received)), ":r": received,
            ":fs": finishing_status, ":fc": finishing_comment,
            ":sr": Decimal(str(scrap_received)), ":su": scrap_usable, ":sn": scrap_notes})

@_write_and_clear
def update_service_po_status(po_id, status):
    db = get_dynamodb()
    table = db.Table(TABLES["service_po"])
    table.update_item(Key={"po_id": po_id},
        UpdateExpression="SET #s = :s, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":u": _now()})

@_write_and_clear
def place_service_po_via_sqs(po_id, vendor_email, vendor_name, services, total_amount, payment_terms, expected_delivery):
    """Place Service PO: update status + send email to vendor + notify management."""
    update_service_po_status(po_id, "Placed")
    po_data = {"po_id": po_id, "vendor_name": vendor_name, "payment_terms": payment_terms,
               "expected_delivery": expected_delivery, "total_amount": total_amount}
    spo_items = get_service_po_items(po_id)
    if vendor_email:
        send_po_email(po_data, spo_items or services, vendor_email, "Service")
    cfg = get_email_config()
    mgmt = cfg.get("management_emails", [])
    sender = cfg.get("sender_email", "")
    company = cfg.get("company_name", "FabriFlow")
    if mgmt and sender:
        html = f"""<div style="font-family:Arial,sans-serif;padding:20px">
            <h3>🛠️ Service PO Placed: {po_id}</h3>
            <p><strong>Vendor:</strong> {vendor_name}</p>
            <p><strong>Amount:</strong> ₹{total_amount:,.2f}</p>
            <p><strong>Payment:</strong> {payment_terms}</p>
            <p><strong>Expected Return:</strong> {expected_delivery}</p>
            <p style="color:#64748b;font-size:0.85rem">— {company} ERP</p>
        </div>"""
        for m in mgmt:
            if m:
                send_email(m, f"Service PO Placed: {po_id} — {vendor_name}", html, sender)
    return True


# ═══════════════════════════════════════════════════════════════════
#  INVENTORY
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def add_inventory_item(master_item_id, item_name, category, sub_category,
                       specification, quantity, unit, location, price, remarks=""):
    """Add a brand new inventory item (used when manually adding stock)."""
    db = get_dynamodb()
    table = db.Table(TABLES["inventory"])
    item = _to_decimal({
        "item_id": _gen_id("INV-"), "master_item_id": master_item_id,
        "item_name": item_name, "category": category,
        "sub_category": sub_category, "specification": specification,
        "quantity": quantity, "unit": unit, "location": location,
        "price": price, "remarks": remarks, "updated_at": _now(),
    })
    table.put_item(Item=item)
    return _from_decimal(item)


@_write_and_clear
def receive_to_inventory(item_name, category, sub_category, specification, quantity, unit,
                         location="Main Store", price=0):
    """Receive material into inventory. Aggregates with existing stock by
    item_name + specification + category (vendor-agnostic).
    If a matching entry exists, increments its quantity.
    If not, creates a new entry."""
    inventory = get_all_inventory()
    # Find match by name + spec + category (ignore vendor)
    for inv in inventory:
        if (inv.get("item_name", "").lower().strip() == item_name.lower().strip()
            and inv.get("specification", "").lower().strip() == specification.lower().strip()
            and inv.get("category", "").lower().strip() == category.lower().strip()):
            # Found match — increment quantity
            update_inventory_qty(inv["item_id"], quantity)
            return inv
    # No match — create new entry (no vendor field)
    return add_inventory_item("", item_name, category, sub_category,
                              specification, quantity, unit, location, price)

def get_all_inventory():
    return _get_from_run_store("inventory", lambda: _cached_scan(TABLES["inventory"]))

def get_inventory_item(item_id):
    return _cached_get_item(TABLES["inventory"], json.dumps({"item_id": item_id}))

@_write_and_clear
def update_inventory_qty(item_id, quantity_change):
    db = get_dynamodb()
    table = db.Table(TABLES["inventory"])
    table.update_item(Key={"item_id": item_id},
        UpdateExpression="SET quantity = quantity + :q, updated_at = :u",
        ExpressionAttributeValues={":q": Decimal(str(quantity_change)), ":u": _now()})
    # Auto-delete if stock is now 0 or below
    resp = table.get_item(Key={"item_id": item_id})
    item = resp.get("Item")
    if item and float(item.get("quantity", 0)) <= 0:
        table.delete_item(Key={"item_id": item_id})

@_write_and_clear
def delete_inventory_item(item_id):
    db = get_dynamodb()
    table = db.Table(TABLES["inventory"])
    table.delete_item(Key={"item_id": item_id})


# ═══════════════════════════════════════════════════════════════════
#  PRODUCTION TRACKING
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def create_production_tracker(project_id, product_name, product_type, quantity, stages):
    db = get_dynamodb()
    table = db.Table(TABLES["production_tracking"])
    stage_data = {sn: ss[0] for sn, ss in stages}
    item = _to_decimal({"project_id": project_id, "product_id": _gen_id("PRD-"),
            "product_name": product_name, "product_type": product_type,
            "quantity": quantity, "stages": stage_data, "created_at": _now(), "updated_at": _now()})
    table.put_item(Item=item)
    return _from_decimal(item)

def get_production_trackers(project_id):
    return _cached_query(TABLES["production_tracking"], "project_id", project_id)

@_write_and_clear
def update_production_stage(project_id, product_id, stage_name, new_status):
    db = get_dynamodb()
    table = db.Table(TABLES["production_tracking"])
    table.update_item(Key={"project_id": project_id, "product_id": product_id},
        UpdateExpression="SET stages.#sn = :ns, updated_at = :u",
        ExpressionAttributeNames={"#sn": stage_name},
        ExpressionAttributeValues={":ns": new_status, ":u": _now()})


# ═══════════════════════════════════════════════════════════════════
#  MATERIAL ISSUES
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def create_material_issue(project_id, product_id, items, issued_by):
    db = get_dynamodb()
    table = db.Table(TABLES["material_issues"])
    issue = {"issue_id": _gen_id("ISS-"), "project_id": project_id,
             "product_id": product_id, "line_items": _to_decimal(items),
             "issued_by": issued_by, "issued_at": _now()}
    table.put_item(Item=issue)
    # Per-item records in erp_issued_material (auto-expire via DynamoDB TTL after 15 days)
    issued_table = db.Table(TABLES["issued_material"])
    expires_at = int((datetime.utcnow() + timedelta(days=15)).timestamp())
    for it in items:
        issued_table.put_item(Item=_to_decimal({
            "record_id": _gen_id("IMR-"), "issue_id": issue["issue_id"],
            "project_id": project_id, "product_id": product_id,
            "item_name": it.get("item_name", ""),
            "specification": it.get("specification", ""),
            "category": it.get("category", ""),
            "sub_category": it.get("sub_category", ""),
            "quantity": it["quantity"], "unit": it.get("unit", ""),
            "issued_by": issued_by, "issued_at": _now(),
            "expires_at": expires_at,   # DynamoDB TTL attribute — auto-deleted after 15 days
        }))
        update_inventory_qty(it["item_id"], -it["quantity"])
    return _from_decimal(issue)


def get_all_issued_material():
    """All issued-material records (auto-purged by DynamoDB TTL after 15 days)."""
    items = _get_from_run_store("issued_material", lambda: _cached_scan(TABLES["issued_material"]))
    return [i for i in items if i.get("quantity", 0) > 0]


@_write_and_clear
def return_issued_material(record, return_qty, returned_by, reason=""):
    """Return part/all of an issued record back to raw inventory.
    Adds qty back via receive_to_inventory (aggregating), then decrements
    the issued record — deleting it entirely if fully returned."""
    receive_to_inventory(
        record.get("item_name", ""), record.get("category", "Returned"),
        record.get("sub_category", ""), record.get("specification", ""),
        return_qty, record.get("unit", "Nos"))
    db = get_dynamodb()
    table = db.Table(TABLES["issued_material"])
    remaining = float(record.get("quantity", 0)) - float(return_qty)
    if remaining <= 0:
        table.delete_item(Key={"record_id": record["record_id"]})
    else:
        table.update_item(Key={"record_id": record["record_id"]},
            UpdateExpression="SET quantity = :q, last_return = :r",
            ExpressionAttributeValues={":q": Decimal(str(remaining)),
                ":r": f"{return_qty} by {returned_by}: {reason}"[:200]})

def get_material_issues(project_id=None):
    if project_id:
        db = get_dynamodb()
        table = db.Table(TABLES["material_issues"])
        resp = table.scan(FilterExpression=Attr("project_id").eq(project_id))
        return [_from_decimal(i) for i in resp.get("Items", [])]
    return _get_from_run_store("mat_issues", lambda: _cached_scan(TABLES["material_issues"]))


# ═══════════════════════════════════════════════════════════════════
#  FINISHED GOODS & DISPATCH
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def add_finished_good(project_id, product_id, product_name, quantity, notes=""):
    db = get_dynamodb()
    table = db.Table(TABLES["finished_goods"])
    item = _to_decimal({"fg_id": _gen_id("FG-"), "project_id": project_id,
            "product_id": product_id, "product_name": product_name,
            "quantity": quantity, "notes": notes, "status": "In Store", "added_at": _now()})
    table.put_item(Item=item)
    return _from_decimal(item)

def get_finished_goods(project_id=None):
    if project_id:
        db = get_dynamodb()
        table = db.Table(TABLES["finished_goods"])
        resp = table.scan(FilterExpression=Attr("project_id").eq(project_id))
        return [_from_decimal(i) for i in resp.get("Items", [])]
    return _get_from_run_store("fg", lambda: _cached_scan(TABLES["finished_goods"]))

@_write_and_clear
def update_finished_good_status(fg_id, status):
    db = get_dynamodb()
    table = db.Table(TABLES["finished_goods"])
    table.update_item(Key={"fg_id": fg_id},
        UpdateExpression="SET #s = :s", ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status})

@_write_and_clear
def dispatch_goods(project_id, fg_ids, dispatch_to, vehicle_no, notes=""):
    db = get_dynamodb()
    table = db.Table(TABLES["dispatched_goods"])
    item = {"dispatch_id": _gen_id("DSP-"), "project_id": project_id, "fg_ids": fg_ids,
            "dispatch_to": dispatch_to, "vehicle_no": vehicle_no, "notes": notes, "dispatched_at": _now()}
    table.put_item(Item=item)
    for fg_id in fg_ids:
        update_finished_good_status(fg_id, "Dispatched")
    return item

def get_dispatched_goods(project_id=None):
    if project_id:
        db = get_dynamodb()
        table = db.Table(TABLES["dispatched_goods"])
        resp = table.scan(FilterExpression=Attr("project_id").eq(project_id))
        return [_from_decimal(i) for i in resp.get("Items", [])]
    return _get_from_run_store("dispatched", lambda: _cached_scan(TABLES["dispatched_goods"]))

# ═══════════════════════════════════════════════════════════════════
#  SCRAP INVENTORY
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def add_scrap_item(item_name, category, specification, quantity, unit, source_po="", notes=""):
    db = get_dynamodb()
    table = db.Table(TABLES["scrap_inventory"])
    item = _to_decimal({
        "item_id": _gen_id("SCR-"), "item_name": item_name, "category": category,
        "specification": specification, "quantity": quantity, "unit": unit,
        "source_po": source_po, "notes": notes, "added_at": _now(),
    })
    table.put_item(Item=item)
    return _from_decimal(item)

def get_all_scrap():
    return _get_from_run_store("scrap", lambda: _cached_scan(TABLES["scrap_inventory"]))

@_write_and_clear
def update_scrap_qty(item_id, quantity_change):
    db = get_dynamodb()
    table = db.Table(TABLES["scrap_inventory"])
    table.update_item(Key={"item_id": item_id},
        UpdateExpression="SET quantity = quantity + :q",
        ExpressionAttributeValues={":q": Decimal(str(quantity_change))})
    # Auto-delete if stock is now 0 or below
    resp = table.get_item(Key={"item_id": item_id})
    item = resp.get("Item")
    if item and float(item.get("quantity", 0)) <= 0:
        table.delete_item(Key={"item_id": item_id})

@_write_and_clear
def delete_scrap_item(item_id):
    db = get_dynamodb()
    table = db.Table(TABLES["scrap_inventory"])
    table.delete_item(Key={"item_id": item_id})


# ═══════════════════════════════════════════════════════════════════
#  SERVICE PO — Inventory Linkage (issue material to service vendor)
# ═══════════════════════════════════════════════════════════════════
@_write_and_clear
def issue_material_to_service_vendor(po_id, inventory_items):
    """Deduct material from raw inventory or scrap store for a service PO.
    Routes to correct table based on item source."""
    for it in inventory_items:
        source = it.get("source", "Raw Material")
        if source == "Scrap Store" or it.get("item_id", "").startswith("SCR-"):
            update_scrap_qty(it["item_id"], -it["quantity"])
        else:
            update_inventory_qty(it["item_id"], -it["quantity"])
    # Store what was issued on the PO record
    db = get_dynamodb()
    table = db.Table(TABLES["service_po"])
    table.update_item(Key={"po_id": po_id},
        UpdateExpression="SET issued_material = :im, updated_at = :u",
        ExpressionAttributeValues={":im": _to_decimal(inventory_items), ":u": _now()})


def receive_scrap_from_service(po_id, scrap_items):
    """Receive scrap back from service vendor. Adds to scrap inventory.
    scrap_items = [{"item_name": "...", "quantity": 5, "unit": "Kg", "notes": "offcuts"}, ...]"""
    for item in scrap_items:
        add_scrap_item(
            item.get("item_name", ""), item.get("category", "Scrap"),
            item.get("specification", ""), item.get("quantity", 0),
            item.get("unit", "Kg"), po_id, item.get("notes", ""),
        )
