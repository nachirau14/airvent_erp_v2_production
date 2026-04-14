"""
Shared configuration for Fabrication ERP System.
Copy this file into both management_app/ and production_app/
"""

# DynamoDB Table Names
TABLES = {
    "master_items": "erp_master_items",
    "projects": "erp_projects",
    "boq_items": "erp_boq_items",
    "inventory": "erp_inventory",
    "vendors": "erp_vendors",
    "service_vendors": "erp_service_vendors",
    "service_vendor_services": "erp_service_vendor_services",
    "raw_material_po": "erp_raw_material_purchase_orders",
    "raw_material_po_items": "erp_raw_material_po_items",
    "service_po": "erp_service_purchase_orders",
    "service_po_items": "erp_service_po_items",
    "production_tracking": "erp_production_tracking",
    "finished_goods": "erp_finished_goods",
    "dispatched_goods": "erp_dispatched_goods",
    "material_issues": "erp_material_issues",
    "order_staging": "erp_order_staging",
    "email_config": "erp_email_config",
}

# Material Categories
MATERIAL_CATEGORIES = {
    "Mild Steel": ["Sheet", "Angle", "Flat", "Square Tube", "Pipe", "Channel", "Beam", "Round Bar"],
    "Stainless Steel 304": ["Sheet", "Angle", "Flat", "Square Tube", "Pipe"],
    "Stainless Steel 202": ["Sheet", "Angle", "Flat", "Square Tube", "Pipe"],
    "Components": ["Sensor", "Control Panel", "Electrical Cable", "Cable Tray", "Motor", "Gearbox", "Actuator", "VFD", "PLC", "Switch", "Relay"],
    "Fasteners": ["Bolt", "Nut", "Washer", "Rivet", "Anchor"],
    "Consumables": ["Welding Spool", "TIG Rod", "MIG Wire", "Welding Electrode", "Grinding Wheel", "Cut-off Wheel", "Flap Disc", "Primer", "Paint", "Thinner"],
    "Safety": ["Gloves", "Safety Glass", "Welding Shield", "Ear Plug"],
    "Services": ["Laser Cutting", "Bending", "Rolling", "CNC Machining", "Turning", "Milling", "Surface Treatment", "Galvanizing", "Powder Coating", "Zinc Plating", "Sandblasting"],
    "Other": ["Custom"],
}

PAYMENT_TERMS = ["Credit - 30 Days", "Credit - 45 Days", "Credit - 60 Days", "Advance Payment", "On Receipt", "50% Advance + 50% On Delivery", "COD"]
UNITS_OF_MEASURE = ["Kg", "Nos", "Meters", "Sq.Meters", "Liters", "Sets", "Lots", "Feet", "Sq.Feet", "Rolls", "Pcs"]

PRODUCTION_STAGES = {
    "Bagfilter": [
        ("Raw Material", ["Pending", "Ordered", "Received"]),
        ("Laser Cutting", ["Pending", "Issued", "Received"]),
        ("Bending", ["Issued", "In Progress", "Complete"]),
        ("Setting", ["Issued", "In Progress", "Complete"]),
        ("Structure", ["Issued", "In Progress", "Complete"]),
        ("Platform", ["Issued", "In Progress", "Complete"]),
        ("Railings", ["Issued", "In Progress", "Complete"]),
        ("Ladder", ["Issued", "In Progress", "Complete"]),
        ("Painting", ["Issued", "In Progress", "Complete"]),
        ("Assembly", ["Issued", "In Progress", "Complete"]),
        ("Packing", ["In Progress", "Complete"]),
        ("Complete", ["Pending", "Complete"]),
    ],
    "Conveyor": [
        ("Raw Material", ["Pending", "Ordered", "Received"]),
        ("Laser Cutting", ["Pending", "Issued", "Received"]),
        ("Bending", ["Issued", "In Progress", "Complete"]),
        ("Frame Fabrication", ["Issued", "In Progress", "Complete"]),
        ("Roller Assembly", ["Issued", "In Progress", "Complete"]),
        ("Belt Installation", ["Issued", "In Progress", "Complete"]),
        ("Motor Mounting", ["Issued", "In Progress", "Complete"]),
        ("Wiring", ["Issued", "In Progress", "Complete"]),
        ("Painting", ["Issued", "In Progress", "Complete"]),
        ("Testing", ["Issued", "In Progress", "Complete"]),
        ("Packing", ["In Progress", "Complete"]),
        ("Complete", ["Pending", "Complete"]),
    ],
    "Ducting": [
        ("Raw Material", ["Pending", "Ordered", "Received"]),
        ("Cutting", ["Pending", "Issued", "Received"]),
        ("Rolling", ["Issued", "In Progress", "Complete"]),
        ("Welding", ["Issued", "In Progress", "Complete"]),
        ("Flanging", ["Issued", "In Progress", "Complete"]),
        ("Painting", ["Issued", "In Progress", "Complete"]),
        ("Packing", ["In Progress", "Complete"]),
        ("Complete", ["Pending", "Complete"]),
    ],
    "Custom": [
        ("Raw Material", ["Pending", "Ordered", "Received"]),
        ("Cutting", ["Pending", "Issued", "Received"]),
        ("Fabrication", ["Issued", "In Progress", "Complete"]),
        ("Welding", ["Issued", "In Progress", "Complete"]),
        ("Finishing", ["Issued", "In Progress", "Complete"]),
        ("Painting", ["Issued", "In Progress", "Complete"]),
        ("Assembly", ["Issued", "In Progress", "Complete"]),
        ("Testing", ["Issued", "In Progress", "Complete"]),
        ("Packing", ["In Progress", "Complete"]),
        ("Complete", ["Pending", "Complete"]),
    ],
}

PO_STATUSES = ["Draft", "Placed", "Partially Received", "Complete", "Cancelled"]
SERVICE_PO_STATUSES = ["Draft", "Placed", "In Progress", "Partially Received", "Complete", "Cancelled"]
PROJECT_STATUSES = ["Planning", "BOQ Ready", "Procurement", "In Production", "Complete", "Dispatched"]
