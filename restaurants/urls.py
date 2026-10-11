from django.urls import path
from .views_auth import merchant_login, merchant_logout
from .views import (
    merchant_dashboard, 
    customer_storefront, place_order, order_success,
    edit_menu_item, delete_menu_item, 
    edit_category, delete_category,
    merchant_orders, update_order_status,
    api_order_status,
    merchant_tables, delete_table,
    table_bill_summary, checkout_table_order,
    merchant_table_receipt,
    update_order_item_status,
    cancel_order_item_customer,
    update_order_item_quantity_customer,
    merchant_table_status_manage,
    merchant_accounting_report,
    delete_transaction,
    landing_page,
)

urlpatterns = [

    path('', landing_page, name='landing_page'),
    # ระบบล็อกอิน / ออกจากระบบ
    path('login/', merchant_login, name='merchant_login'),
    path('logout/', merchant_logout, name='merchant_logout'),

    # หน้าร้านสำหรับลูกค้าแบบระบุโต๊ะ / โซน (ต้องมีชื่อ path ตรงกับใน HTML)
    path('store/<slug:slug>/table/<str:table_number>/', customer_storefront, name='customer_storefront_table'),
    path('store/<slug:slug>/table/<str:table_number>/order/', place_order, name='place_order_table'),
    path('store/<slug:slug>/table/<str:table_number>/success/<int:order_id>/', order_success, name='order_success_table'),

    # API สำหรับดึงสถานะออร์เดอร์แบบ Real-time (AJAX)
    path('store/<slug:slug>/api/order/<int:order_id>/status/', api_order_status, name='api_order_status'),

    # หน้าหลังบ้านสำหรับเจ้าของร้าน
    path('store/<slug:slug>/manage/', merchant_dashboard, name='merchant_dashboard'),
    path('store/<slug:slug>/manage/tables/', merchant_tables, name='merchant_tables'),
    path('store/<slug:slug>/manage/tables/<int:table_id>/delete/', delete_table, name='delete_table'),
    path('store/<slug:slug>/manage/orders/', merchant_orders, name='merchant_orders'),
    path('store/<slug:slug>/manage/orders/<int:order_id>/update/', update_order_status, name='update_order_status'),
    path('store/<slug:slug>/manage/menu/<int:pk>/edit/', edit_menu_item, name='edit_menu_item'),
    path('store/<slug:slug>/manage/menu/<int:pk>/delete/', delete_menu_item, name='delete_menu_item'),
    path('store/<slug:slug>/manage/category/<int:pk>/edit/', edit_category, name='edit_category'),
    path('store/<slug:slug>/manage/category/<int:pk>/delete/', delete_category, name='delete_category'),

    path('store/<slug:slug>/table/<str:table_number>/bill/', table_bill_summary, name='table_bill_summary'),
    path('store/<slug:slug>/manage/table/<str:table_number>/checkout/', checkout_table_order, name='checkout_table_order'),

    path('store/<slug:slug>/manage/table/<str:table_number>/receipt/', merchant_table_receipt, name='merchant_table_receipt'),

    path('store/<slug:slug>/manage/item/<int:item_id>/update/', update_order_item_status, name='update_order_item_status'),
    path('store/<slug:slug>/item/<int:item_id>/cancel/', cancel_order_item_customer, name='cancel_order_item_customer'),
    path('store/<slug:slug>/item/<int:item_id>/update-qty/', update_order_item_quantity_customer, name='update_order_item_quantity_customer'),

    path('store/<slug:slug>/manage/table/<str:table_number>/status/', merchant_table_status_manage, name='merchant_table_status_manage'),

    path('store/<slug:slug>/manage/accounting/', merchant_accounting_report, name='merchant_accounting_report'),
    path('store/<slug:slug>/manage/accounting/transaction/<int:pk>/delete/', delete_transaction, name='delete_transaction'),



]