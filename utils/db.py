"""
Database layer — DynamoDB CRUD operations.
Reads AWS credentials from Streamlit secrets.
"""
import boto3
import uuid
import json
import streamlit as st
from datetime import datetime
from decimal import Decimal
from boto3.dynamodb.conditions import Key, Attr
from config import TABLES


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
def get_sqs_client():
    return boto3.client("sqs", **_get_aws_config())

def _gen_id(prefix=""):
    return f"{prefix}{uuid.uuid4().hex[:12]}"

def _now():
    return datetime.utcnow().isoformat()

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
#  MASTER ITEMS
# ═══════════════════════════════════════════════════════════════════
def add_master_item(item_name, vendor, category, sub_category, specification, unit, location, price, revised_price=0, remarks=""):
    db = get_dynamodb()
    table = db.Table(TABLES["master_items"])
    item = _to_decimal({
        "item_id": _gen_id("MI-"),
        "item_name": item_name,
        "vendor": vendor,
        "category": category,
        "sub_category": sub_category,
        "specification": specification,
        "unit": unit,
        "location": location,
        "price": price,
        "revised_price": revised_price,
        "remarks": remarks,
        "created_at": _now(),
        "updated_at": _now(),
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
    table.update_item(
        Key={"item_id": item_id},
        UpdateExpression="SET " + ", ".join(expr_parts),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )

def delete_master_item(item_id):
    db = get_dynamodb()
    table = db.Table(TABLES["master_items"])
    table.delete_item(Key={"item_id": item_id})

def get_master_items_by_vendor(vendor_name):
    items = get_all_master_items()
    return [i for i in items if i.get("vendor", "").lower() == vendor_name.lower()]

def get_master_items_by_category(category):
    items = get_all_master_items()
    return [i for i in items if i.get("category", "") == category]

def bulk_upload_master_items(items_list):
    results = []
    for item in items_list:
        r = add_master_item(
            item.get("item_name", ""), item.get("vendor", ""),
            item.get("category", ""), item.get("sub_category", ""),
            item.get("specification", ""), item.get("unit", "Nos"),
            item.get("location", "Main Store"),
            float(item.get("price", 0)), float(item.get("revised_price", 0)),
            item.get("remarks", ""),
        )
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
#  BOQ ITEMS (linked to master items)
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
def add_vendor(name, contact_person, phone, email, address, gst_no, payment_terms):
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
#  ORDER STAGING (BOQ → grouped POs per vendor)
# ═══════════════════════════════════════════════════════════════════
def create_staged_orders_from_boq(project_id):
    """Group BOQ items by vendor and create staged PO entries."""
    boq_items = get_boq_items(project_id)
    db = get_dynamodb()
    table = db.Table(TABLES["order_staging"])
    # Group by vendor
    vendor_groups = {}
    for item in boq_items:
        vendor = item.get("vendor", "Unknown")
        if vendor not in vendor_groups:
            vendor_groups[vendor] = []
        vendor_groups[vendor].append(item)
    staged = []
    for vendor_name, items in vendor_groups.items():
        stage_id = _gen_id("STG-")
        total = sum(i.get("total", 0) for i in items)
        entry = _to_decimal({
            "stage_id": stage_id, "project_id": project_id,
            "vendor_name": vendor_name,
            "items": items, "total_amount": total,
            "status": "Staged", "created_at": _now(), "updated_at": _now(),
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

def update_staged_order_items(stage_id, items, total_amount):
    db = get_dynamodb()
    table = db.Table(TABLES["order_staging"])
    table.update_item(Key={"stage_id": stage_id},
        UpdateExpression="SET items = :i, total_amount = :t, updated_at = :u",
        ExpressionAttributeValues={":i": _to_decimal(items),
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
            "created_at": _now(), "updated_at": _now()})
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
            "created_at": _now(), "updated_at": _now()})
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
#  INVENTORY (linked to master items)
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
             "product_id": product_id, "items": _to_decimal(items),
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
