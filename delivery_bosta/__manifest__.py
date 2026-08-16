{
    'name': 'leap Bosta Delivery Integration',
    'version': '19.0.1.0.0',
    'summary': 'Bosta Shipping Carrier | تكامل شركة بوستا للشحن',
    'description': '''
Full integration between Bosta shipping carrier and Odoo 19.
Create shipments, print AWB labels, track parcels and schedule pickups
— all from within Odoo without touching the Bosta dashboard.
    ''',
    'author': 'leapai.ai',
    'website': 'https://leapai.ai',
    'maintainer': 'a.osman@bab.com.sa',
    'support': 'abdzoro89@gmail.com',
    'category': 'Inventory/Delivery',
    'license': 'LGPL-3',
    'depends': [
        'stock_delivery',
        'sale_management',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/delivery_bosta_data.xml',
        'views/delivery_carrier_views.xml',
        'views/stock_picking_views.xml',
        'views/sale_order_views.xml',
        'views/bosta_menu_views.xml',
        'wizard/bosta_pickup_wizard_views.xml',
        'report/report_bosta_awb.xml',
    ],
    'images': [
        'static/description/icon.png',
        'static/description/screenshot_01_carrier.png',
        'static/description/screenshot_02_picking.png',
        'static/description/screenshot_03_tracking.png',
        'static/description/screenshot_04_wizard.png',
    ],
    'external_dependencies': {'python': ['requests']},
    'installable': True,
    'application': True,
    'auto_install': False,
}
