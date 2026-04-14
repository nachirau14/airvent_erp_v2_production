"""
Database layer — DynamoDB + S3 operations.
Reads AWS credentials from Streamlit secrets.
"""
import boto3
import uuid
import json
import io
import streamlit as st
from datetime import datetime
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


# ═══════════════════════════════════════════════════════════════════
#  S3 — PDF Generation & Attachments
# ═══════════════════════════════════════════════════════════════════
def generate_po_pdf(po_data, po_items, po_type="Material"):
    """Generate a simple PDF for a PO and upload to S3. Returns the S3 key."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
    except ImportError:
        return None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    elements = []

    # Header
    elements.append(Paragraph(f"<b>PURCHASE ORDER</b>", styles["Title"]))
    elements.append(Spacer(1, 5*mm))
    po_id = po_data.get("po_id", "")
    elements.append(Paragraph(f"<b>PO Number:</b> {po_id}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Type:</b> {po_type}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Vendor:</b> {po_data.get('vendor_name', '')}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Payment Terms:</b> {po_data.get('payment_terms', '')}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Expected Delivery:</b> {po_data.get('expected_delivery', '')}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Date:</b> {_today()}", styles["Normal"]))
    elements.append(Spacer(1, 8*mm))

    # Items table
    data = [["#", "Description", "Specification", "Qty", "Unit", "Rate (₹)", "Amount (₹)"]]
    for idx, item in enumerate(po_items, 1):
        qty = item.get("quantity", 0)
        rate = item.get("unit_price", item.get("rate", 0))
        data.append([str(idx), item.get("description", ""), item.get("specification", ""),
                      str(qty), item.get("unit", ""), f"{rate:,.2f}", f"{qty * rate:,.2f}"])

    total = sum(i.get("quantity", 0) * i.get("unit_price", i.get("rate", 0)) for i in po_items)
    data.append(["", "", "", "", "", "Total", f"{total:,.2f}"])

    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e40af")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f1f5f9")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 10*mm))
    if po_data.get("notes"):
        elements.append(Paragraph(f"<b>Notes:</b> {po_data['notes']}", styles["Normal"]))

    doc.build(elements)
    buf.seek(0)

    # Upload to S3
    s3_key = f"po/{po_type.lower()}/{_today()}/{po_id}.pdf"
    try:
        s3 = get_s3_client()
        s3.put_object(Bucket=S3_PO_PDF_BUCKET, Key=s3_key, Body=buf.getvalue(),
                       ContentType="application/pdf")
        return s3_key
    except Exception as e:
        st.warning(f"S3 upload failed: {e}")
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
def add_master_item(item_name, vendor, category, sub_category, specification, unit, location, price, revised_price=0, remarks=""):
    db = get_dynamodb()
    table = db.Table(TABLES["master_items"])
    item = _to_decimal({
        "item_id": _gen_id("MI-"), "item_name": item_name, "vendor": vendor,
        "category": category, "sub_category": sub_category, "specification": specification,
        "unit": unit, "location": location, "price": price, "revised_price": revised_price,
        "remarks": remarks, "created_at": _now(), "updated_at": _now(),
    })
    table.put_item(Item=item)
    return _from_decimal(item)

def get_all_master_items():
    return _scan_all(TABLES["master_items"])

def get_master_item(item_id):
    db = get_dynamodb()
    table = db.Table(TABLES["master_items"])
    resp = table.get_item(Key={"item_id": item_id})
    item = resp.get("Item")
    return _from_decimal(item) if item else None

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

def delete_master_item(item_id):
    db = get_dynamodb()
    table = db.Table(TABLES["master_items"])
    table.delete_item(Key={"item_id": item_id})

def get_master_items_by_vendor(vendor_name):
    items = get_all_master_items()
    return [i for i in items if i.get("vendor", "").lower() == vendor_name.lower()]

def bulk_upload_master_items(items_list):
    results = []
    for item in items_list:
        r = add_master_item(item.get("item_name", ""), item.get("vendor", ""),
            item.get("category", ""), item.get("sub_category", ""),
            item.get("specification", ""), item.get("unit", "Nos"),
            item.get("location", "Main Store"), float(item.get("price", 0)),
            float(item.get("revised_price", 0)), item.get("remarks", ""))
        results.append(r)
    return results


# ═══════════════════════════════════════════════════════════════════
#  PROJECTS
# ═══════════════════════════════════════════════════════════════════
def create_project(name, client_name, description, product_type, status="Planning"):
    db = get_dynamodb()
    table = db.Table(TABLES["projects"])
    item = {"project_id": _gen_id("PRJ-"), "name": name, "client_name": client_name,
            "description": description, "product_type": product_type, "status": status,
            "created_at": _now(), "updated_at": _now()}
    table.put_item(Item=item)
    return item

def get_all_projects():
    return _scan_all(TABLES["projects"])

def get_project(project_id):
    db = get_dynamodb()
    table = db.Table(TABLES["projects"])
    resp = table.get_item(Key={"project_id": project_id})
    item = resp.get("Item")
    return _from_decimal(item) if item else None

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
def add_boq_item(project_id, master_item_id, item_name, vendor, category, sub_category,
                 specification, quantity, unit, rate):
    db = get_dynamodb()
    table = db.Table(TABLES["boq_items"])
    item = _to_decimal({
        "project_id": project_id, "item_id": _gen_id("BOQ-"),
        "master_item_id": master_item_id, "item_name": item_name, "vendor": vendor,
        "category": category, "sub_category": sub_category,
        "specification": specification, "quantity": quantity, "unit": unit,
        "rate": rate, "total": quantity * rate, "created_at": _now(),
    })
    table.put_item(Item=item)
    return _from_decimal(item)

def get_boq_items(project_id):
    db = get_dynamodb()
    table = db.Table(TABLES["boq_items"])
    resp = table.query(KeyConditionExpression=Key("project_id").eq(project_id))
    return [_from_decimal(i) for i in resp.get("Items", [])]

def delete_boq_item(project_id, item_id):
    db = get_dynamodb()
    table = db.Table(TABLES["boq_items"])
    table.delete_item(Key={"project_id": project_id, "item_id": item_id})

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
def add_vendor(name, contact_person="—", phone="—", email="—", address="—", gst_no="—", payment_terms="Credit - 30 Days"):
    db = get_dynamodb()
    table = db.Table(TABLES["vendors"])
    item = {"vendor_id": _gen_id("VEN-"), "name": name, "contact_person": contact_person,
            "phone": phone, "email": email, "address": address, "gst_no": gst_no,
            "payment_terms": payment_terms, "created_at": _now()}
    table.put_item(Item=item)
    return item

def get_all_vendors():
    return _scan_all(TABLES["vendors"])

def get_vendor(vendor_id):
    db = get_dynamodb()
    table = db.Table(TABLES["vendors"])
    resp = table.get_item(Key={"vendor_id": vendor_id})
    item = resp.get("Item")
    return _from_decimal(item) if item else None

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

def ensure_vendor_exists(vendor_name):
    """Auto-create vendor if it doesn't exist. Returns vendor record."""
    vendors = get_all_vendors()
    for v in vendors:
        if v.get("name", "").lower().strip() == vendor_name.lower().strip():
            return v
    return add_vendor(vendor_name)


# ═══════════════════════════════════════════════════════════════════
#  SERVICE VENDORS
# ═══════════════════════════════════════════════════════════════════
def add_service_vendor(name, contact_person, phone, email, address, gst_no, payment_terms):
    db = get_dynamodb()
    table = db.Table(TABLES["service_vendors"])
    item = {"vendor_id": _gen_id("SV-"), "name": name, "contact_person": contact_person,
            "phone": phone, "email": email, "address": address, "gst_no": gst_no,
            "payment_terms": payment_terms, "created_at": _now()}
    table.put_item(Item=item)
    return item

def get_all_service_vendors():
    return _scan_all(TABLES["service_vendors"])

def get_service_vendor(vendor_id):
    db = get_dynamodb()
    table = db.Table(TABLES["service_vendors"])
    resp = table.get_item(Key={"vendor_id": vendor_id})
    item = resp.get("Item")
    return _from_decimal(item) if item else None

def add_service_vendor_service(vendor_id, service_name, description, unit, rate):
    db = get_dynamodb()
    table = db.Table(TABLES["service_vendor_services"])
    item = _to_decimal({"vendor_id": vendor_id, "service_id": _gen_id("SVC-"),
            "service_name": service_name, "description": description,
            "unit": unit, "rate": rate, "updated_at": _now()})
    table.put_item(Item=item)
    return _from_decimal(item)

def get_service_vendor_services(vendor_id):
    db = get_dynamodb()
    table = db.Table(TABLES["service_vendor_services"])
    resp = table.query(KeyConditionExpression=Key("vendor_id").eq(vendor_id))
    return [_from_decimal(i) for i in resp.get("Items", [])]


# ═══════════════════════════════════════════════════════════════════
#  ORDER STAGING — fixed reserved keyword 'items'
# ═══════════════════════════════════════════════════════════════════
def create_staged_orders_from_boq(project_id):
    boq_items = get_boq_items(project_id)
    db = get_dynamodb()
    table = db.Table(TABLES["order_staging"])
    vendor_groups = {}
    for item in boq_items:
        vendor = item.get("vendor", "Unknown")
        if vendor not in vendor_groups:
            vendor_groups[vendor] = []
        vendor_groups[vendor].append(item)
    staged = []
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

def update_staged_order_items(stage_id, line_items, total_amount):
    db = get_dynamodb()
    table = db.Table(TABLES["order_staging"])
    table.update_item(Key={"stage_id": stage_id},
        UpdateExpression="SET #li = :i, total_amount = :t, updated_at = :u",
        ExpressionAttributeNames={"#li": "line_items"},
        ExpressionAttributeValues={":i": _to_decimal(line_items),
                                   ":t": Decimal(str(total_amount)), ":u": _now()})

def update_staged_order_status(stage_id, status):
    db = get_dynamodb()
    table = db.Table(TABLES["order_staging"])
    table.update_item(Key={"stage_id": stage_id},
        UpdateExpression="SET #s = :s, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":u": _now()})

def delete_staged_order(stage_id):
    db = get_dynamodb()
    table = db.Table(TABLES["order_staging"])
    table.delete_item(Key={"stage_id": stage_id})


# ═══════════════════════════════════════════════════════════════════
#  RAW MATERIAL PURCHASE ORDERS
# ═══════════════════════════════════════════════════════════════════
def create_raw_material_po(project_id, vendor_id, vendor_name, payment_terms, expected_delivery, items, notes=""):
    db = get_dynamodb()
    po_table = db.Table(TABLES["raw_material_po"])
    items_table = db.Table(TABLES["raw_material_po_items"])
    po_id = _gen_id("RMPO-")
    total_amount = sum(i["quantity"] * i["unit_price"] for i in items)
    po = _to_decimal({"po_id": po_id, "project_id": project_id, "vendor_id": vendor_id,
            "vendor_name": vendor_name, "payment_terms": payment_terms,
            "expected_delivery": expected_delivery, "total_amount": total_amount,
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
    return _scan_all(TABLES["raw_material_po"])

def get_raw_material_po(po_id):
    db = get_dynamodb()
    table = db.Table(TABLES["raw_material_po"])
    resp = table.get_item(Key={"po_id": po_id})
    item = resp.get("Item")
    return _from_decimal(item) if item else None

def get_raw_material_po_items(po_id):
    db = get_dynamodb()
    table = db.Table(TABLES["raw_material_po_items"])
    resp = table.query(KeyConditionExpression=Key("po_id").eq(po_id))
    return [_from_decimal(i) for i in resp.get("Items", [])]

def update_po_item_receipt(po_id, item_id, quantity_received, received=False):
    db = get_dynamodb()
    table = db.Table(TABLES["raw_material_po_items"])
    table.update_item(Key={"po_id": po_id, "item_id": item_id},
        UpdateExpression="SET quantity_received = :qr, received = :r",
        ExpressionAttributeValues={":qr": Decimal(str(quantity_received)), ":r": received})

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

def place_po_via_sqs(po_id, vendor_email, vendor_name, items, total_amount, payment_terms, expected_delivery):
    update_raw_material_po_status(po_id, "Placed")
    return True


# ═══════════════════════════════════════════════════════════════════
#  SERVICE PURCHASE ORDERS
# ═══════════════════════════════════════════════════════════════════
def create_service_po(project_id, vendor_id, vendor_name, payment_terms, expected_delivery, services, notes=""):
    db = get_dynamodb()
    po_table = db.Table(TABLES["service_po"])
    items_table = db.Table(TABLES["service_po_items"])
    po_id = _gen_id("SPO-")
    total_amount = sum(s["quantity"] * s["unit_price"] for s in services)
    po = _to_decimal({"po_id": po_id, "project_id": project_id, "vendor_id": vendor_id,
            "vendor_name": vendor_name, "payment_terms": payment_terms,
            "expected_delivery": expected_delivery, "total_amount": total_amount,
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
    return _scan_all(TABLES["service_po"])

def get_service_po_items(po_id):
    db = get_dynamodb()
    table = db.Table(TABLES["service_po_items"])
    resp = table.query(KeyConditionExpression=Key("po_id").eq(po_id))
    return [_from_decimal(i) for i in resp.get("Items", [])]

def update_service_po_item(po_id, item_id, quantity_received, received, finishing_status, finishing_comment, scrap_received, scrap_usable, scrap_notes):
    db = get_dynamodb()
    table = db.Table(TABLES["service_po_items"])
    table.update_item(Key={"po_id": po_id, "item_id": item_id},
        UpdateExpression="SET quantity_received=:qr, received=:r, finishing_status=:fs, finishing_comment=:fc, scrap_received=:sr, scrap_usable=:su, scrap_notes=:sn",
        ExpressionAttributeValues={":qr": Decimal(str(quantity_received)), ":r": received,
            ":fs": finishing_status, ":fc": finishing_comment,
            ":sr": Decimal(str(scrap_received)), ":su": scrap_usable, ":sn": scrap_notes})

def update_service_po_status(po_id, status):
    db = get_dynamodb()
    table = db.Table(TABLES["service_po"])
    table.update_item(Key={"po_id": po_id},
        UpdateExpression="SET #s = :s, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":u": _now()})

def place_service_po_via_sqs(po_id, vendor_email, vendor_name, services, total_amount, payment_terms, expected_delivery):
    update_service_po_status(po_id, "Placed")
    return True


# ═══════════════════════════════════════════════════════════════════
#  INVENTORY
# ═══════════════════════════════════════════════════════════════════
def add_inventory_item(master_item_id, item_name, vendor, category, sub_category,
                       specification, quantity, unit, location, price, remarks=""):
    db = get_dynamodb()
    table = db.Table(TABLES["inventory"])
    item = _to_decimal({
        "item_id": _gen_id("INV-"), "master_item_id": master_item_id,
        "item_name": item_name, "vendor": vendor, "category": category,
        "sub_category": sub_category, "specification": specification,
        "quantity": quantity, "unit": unit, "location": location,
        "price": price, "remarks": remarks, "updated_at": _now(),
    })
    table.put_item(Item=item)
    return _from_decimal(item)

def get_all_inventory():
    return _scan_all(TABLES["inventory"])

def get_inventory_item(item_id):
    db = get_dynamodb()
    table = db.Table(TABLES["inventory"])
    resp = table.get_item(Key={"item_id": item_id})
    item = resp.get("Item")
    return _from_decimal(item) if item else None

def update_inventory_qty(item_id, quantity_change):
    db = get_dynamodb()
    table = db.Table(TABLES["inventory"])
    table.update_item(Key={"item_id": item_id},
        UpdateExpression="SET quantity = quantity + :q, updated_at = :u",
        ExpressionAttributeValues={":q": Decimal(str(quantity_change)), ":u": _now()})

def delete_inventory_item(item_id):
    db = get_dynamodb()
    table = db.Table(TABLES["inventory"])
    table.delete_item(Key={"item_id": item_id})


# ═══════════════════════════════════════════════════════════════════
#  PRODUCTION TRACKING
# ═══════════════════════════════════════════════════════════════════
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
    db = get_dynamodb()
    table = db.Table(TABLES["production_tracking"])
    resp = table.query(KeyConditionExpression=Key("project_id").eq(project_id))
    return [_from_decimal(i) for i in resp.get("Items", [])]

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
def create_material_issue(project_id, product_id, items, issued_by):
    db = get_dynamodb()
    table = db.Table(TABLES["material_issues"])
    issue = {"issue_id": _gen_id("ISS-"), "project_id": project_id,
             "product_id": product_id, "line_items": _to_decimal(items),
             "issued_by": issued_by, "issued_at": _now()}
    table.put_item(Item=issue)
    for it in items:
        update_inventory_qty(it["item_id"], -it["quantity"])
    return _from_decimal(issue)

def get_material_issues(project_id=None):
    if project_id:
        db = get_dynamodb()
        table = db.Table(TABLES["material_issues"])
        resp = table.scan(FilterExpression=Attr("project_id").eq(project_id))
        return [_from_decimal(i) for i in resp.get("Items", [])]
    return _scan_all(TABLES["material_issues"])


# ═══════════════════════════════════════════════════════════════════
#  FINISHED GOODS & DISPATCH
# ═══════════════════════════════════════════════════════════════════
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
    return _scan_all(TABLES["finished_goods"])

def update_finished_good_status(fg_id, status):
    db = get_dynamodb()
    table = db.Table(TABLES["finished_goods"])
    table.update_item(Key={"fg_id": fg_id},
        UpdateExpression="SET #s = :s", ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status})

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
    return _scan_all(TABLES["dispatched_goods"])
