from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from .models import Restaurant, MenuItem, Category, Order, OrderItem, Table, TransactionRecord, ContactInquiry
from .forms import MenuItemForm, CategoryForm
from django.http import JsonResponse
from django.utils import timezone
from django.core.paginator import Paginator
from django.db.models import Q
from django.db.models.functions import ExtractYear, ExtractMonth, ExtractDay
from django.db.models.functions import TruncDate
from django.db.models import Sum, F


def landing_page(request):
    """ หน้าเว็บไซต์สำหรับโปรโมทและสมัครใช้งานระบบ SaaS """
    submitted = False
    if request.method == 'POST':
        contact_name = request.POST.get('contact_name')
        phone = request.POST.get('phone')
        restaurant_name = request.POST.get('restaurant_name')
        package = request.POST.get('package')
        message = request.POST.get('message', '')

        if contact_name and phone and restaurant_name:
            ContactInquiry.objects.create(
                contact_name=contact_name,
                phone=phone,
                restaurant_name=restaurant_name,
                package=package,
                message=message
            )
            submitted = True  # บันทึกสำเร็จแล้ว ส่งค่านี้ไปบอกหน้าเว็บ
    
    context = {
        'submitted': submitted,
    }
    return render(request, 'restaurants/landing_page.html', context)
    
@login_required(login_url='/login/')
def merchant_dashboard(request, slug):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    
    if restaurant.user != request.user and not request.user.is_superuser:
        return render(request, 'restaurants/403.html', status=403)

    menu_items = restaurant.menu_items.all()
    categories = restaurant.categories.all()

    menu_form = MenuItemForm()
    menu_form.fields['category'].queryset = categories

    if request.method == 'POST':
        if 'add_menu' in request.POST:
            menu_form = MenuItemForm(request.POST, request.FILES)
            if menu_form.is_valid():
                menu_item = menu_form.save(commit=False)
                menu_item.restaurant = restaurant
                menu_item.save()
                return redirect('merchant_dashboard', slug=restaurant.slug)
        
        elif 'add_category' in request.POST:
            cat_name = request.POST.get('category_name')
            if cat_name:
                Category.objects.create(restaurant=restaurant, name=cat_name)
                return redirect('merchant_dashboard', slug=restaurant.slug)

    context = {
        'restaurant': restaurant,
        'menu_items': menu_items,
        'categories': categories,
        'menu_form': menu_form,
    }
    return render(request, 'restaurants/dashboard.html', context)

@login_required(login_url='/login/')
def edit_menu_item(request, slug, pk):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    menu_item = get_object_or_404(MenuItem, pk=pk, restaurant=restaurant)
    
    if request.method == 'POST':
        form = MenuItemForm(request.POST, request.FILES, instance=menu_item)
        if form.is_valid():
            form.save()
            return redirect('merchant_dashboard', slug=restaurant.slug)
    else:
        form = MenuItemForm(instance=menu_item)
        form.fields['category'].queryset = restaurant.categories.all()

    context = {'restaurant': restaurant, 'form': form, 'menu_item': menu_item}
    return render(request, 'restaurants/edit_menu.html', context)

@login_required(login_url='/login/')
def delete_menu_item(request, slug, pk):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    menu_item = get_object_or_404(MenuItem, pk=pk, restaurant=restaurant)
    menu_item.delete()
    return redirect('merchant_dashboard', slug=restaurant.slug)

@login_required(login_url='/login/')
def edit_category(request, slug, pk):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    category = get_object_or_404(Category, pk=pk, restaurant=restaurant)
    
    if request.method == 'POST':
        name = request.POST.get('category_name')
        if name:
            category.name = name
            category.save()
            return redirect('merchant_dashboard', slug=restaurant.slug)
    
    context = {'restaurant': restaurant, 'category': category}
    return render(request, 'restaurants/edit_category.html', context)

@login_required(login_url='/login/')
def delete_category(request, slug, pk):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    category = get_object_or_404(Category, pk=pk, restaurant=restaurant)
    category.delete()
    return redirect('merchant_dashboard', slug=restaurant.slug)


def customer_storefront(request, slug, table_number):
    restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
    categories = restaurant.categories.all()
    menu_items = restaurant.menu_items.filter(is_available=True)
    
    category_id = request.GET.get('category')
    if category_id:
        menu_items = menu_items.filter(category_id=category_id)

    active_orders = restaurant.orders.filter(
        table_number=table_number, 
        status__in=['pending', 'cooking']
    ).order_by('-created_at')

    context = {
        'restaurant': restaurant,
        'categories': categories,
        'menu_items': menu_items,
        'selected_category': category_id,
        'table_number': table_number,
        'active_orders': active_orders,
    }
    return render(request, 'restaurants/storefront.html', context)

def place_order(request, slug, table_number):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    
    if request.method == 'POST':
        customer_name = request.POST.get('customer_name', '')
        menu_item_ids = request.POST.getlist('menu_item_id')
        quantities = request.POST.getlist('quantity')
        
        has_items = False
        for q in quantities:
            if int(q or 0) > 0:
                has_items = True
                break
                
        if not has_items:
            return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)

        order = Order.objects.create(
            restaurant=restaurant,
            table_number=table_number,
            customer_name=customer_name,
            status='pending',
            is_paid=False,
            total_price=0
        )
        
        total_price = 0
        for item_id, qty in zip(menu_item_ids, quantities):
            qty = int(qty or 0)
            if qty > 0:
                menu_item = get_object_or_404(MenuItem, id=item_id, restaurant=restaurant)
                item_total = menu_item.price * qty
                total_price += item_total
                
                OrderItem.objects.create(
                    order=order,
                    menu_item=menu_item,
                    quantity=qty,
                    price=menu_item.price,
                    status='pending'
                )
        
        order.total_price = total_price
        order.save()
        
        return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)
        
    return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)


def order_success(request, slug, table_number, order_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    order = get_object_or_404(Order, id=order_id, restaurant=restaurant)
    context = {'restaurant': restaurant, 'order': order, 'table_number': table_number}
    return render(request, 'restaurants/order_success.html', context)


@login_required(login_url='/login/')
def merchant_orders(request, slug):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    
    if restaurant.user != request.user and not request.user.is_superuser:
        return render(request, 'restaurants/403.html', status=403)

    # 1. โต๊ะที่กำลังใช้บริการอยู่ (Active Tables)
    unpaid_orders = restaurant.orders.filter(is_paid=False).order_by('-created_at')
    
    active_tables_dict = {}
    for order in unpaid_orders:
        t_num = order.table_number
        if t_num not in active_tables_dict:
            active_tables_dict[t_num] = {
                'table_number': t_num,
                'orders': [],
                'total_amount': 0,
                'order_count': 0,
            }
        active_tables_dict[t_num]['orders'].append(order)
        active_tables_dict[t_num]['total_amount'] += order.total_price
        active_tables_dict[t_num]['order_count'] += 1

    active_tables = list(active_tables_dict.values())

    # 2. ประวัติบิลที่ชำระเงินแล้ว พร้อมระบบค้นหา และ Pagination (15 รายการต่อหน้า)
    paid_orders_list = restaurant.orders.filter(is_paid=True).order_by('-created_at')
    
    search_query = request.GET.get('q', '').strip()
    if search_query:
        clean_id = search_query.replace('#', '')
        if clean_id.isdigit():
            paid_orders_list = paid_orders_list.filter(Q(id=clean_id) | Q(table_number__icontains=search_query) | Q(customer_name__icontains=search_query))
        else:
            paid_orders_list = paid_orders_list.filter(
                Q(created_at__icontains=search_query) | 
                Q(table_number__icontains=search_query) | 
                Q(customer_name__icontains=search_query)
            )

    daily_total_sales = sum(o.total_price for o in paid_orders_list)

    paginator = Paginator(paid_orders_list, 5)
    page_number = request.GET.get('page')
    paid_orders_today = paginator.get_page(page_number)

    context = {
        'restaurant': restaurant,
        'active_tables': active_tables,
        'paid_orders_today': paid_orders_today,
        'daily_total_sales': daily_total_sales,
        'search_query': search_query,
    }
    return render(request, 'restaurants/merchant_orders.html', context)


@login_required(login_url='/login/')
def update_order_status(request, slug, order_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    order = get_object_or_404(Order, id=order_id, restaurant=restaurant)
    
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in dict(Order.STATUS_CHOICES):
            order.status = new_status
            order.save()
            
    return redirect('merchant_orders', slug=restaurant.slug)


def api_order_status(request, slug, order_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    order = get_object_or_404(Order, id=order_id, restaurant=restaurant)
    
    data = {
        'status': order.status,
        'status_display': order.get_status_display(),
    }
    return JsonResponse(data)


@login_required(login_url='/login/')
def delete_table(request, slug, table_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    table = get_object_or_404(Table, id=table_id, restaurant=restaurant)
    table.delete()
    return redirect('merchant_tables', slug=restaurant.slug)


@login_required(login_url='/login/')
def checkout_table_order(request, slug, table_number):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    orders = restaurant.orders.filter(table_number=table_number, is_paid=False)
    
    if request.method == 'POST':
        orders.update(is_paid=True, status='completed')
        return redirect('merchant_orders', slug=restaurant.slug)
        
    return redirect('merchant_orders', slug=restaurant.slug)


def table_bill_summary(request, slug, table_number):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    unpaid_orders = restaurant.orders.filter(table_number=table_number, is_paid=False).order_by('created_at')
    grand_total = sum(order.total_price for order in unpaid_orders)

    context = {
        'restaurant': restaurant,
        'table_number': table_number,
        'unpaid_orders': unpaid_orders,
        'grand_total': grand_total,
    }
    return render(request, 'restaurants/bill_summary.html', context)


@login_required(login_url='/login/')
def merchant_tables(request, slug):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    
    if restaurant.user != request.user and not request.user.is_superuser:
        return render(request, 'restaurants/403.html', status=403)

    if request.method == 'POST':
        table_name = request.POST.get('table_name')
        if table_name:
            Table.objects.get_or_create(restaurant=restaurant, name=table_name)
            return redirect('merchant_tables', slug=restaurant.slug)

    tables = restaurant.tables.all()
    context = {
        'restaurant': restaurant,
        'tables': tables,
    }
    return render(request, 'restaurants/merchant_tables.html', context)


@login_required(login_url='/login/')
def merchant_table_receipt(request, slug, table_number):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    
    if restaurant.user != request.user and not request.user.is_superuser:
        return render(request, 'restaurants/403.html', status=403)

    order_id = request.GET.get('order_id')
    
    if order_id:
        table_orders = restaurant.orders.filter(id=order_id, restaurant=restaurant)
    else:
        table_orders = restaurant.orders.filter(table_number=table_number, is_paid=False).order_by('created_at')
        
    grand_total = sum(order.total_price for order in table_orders)

    context = {
        'restaurant': restaurant,
        'table_number': table_number,
        'table_orders': table_orders,
        'grand_total': grand_total,
    }
    return render(request, 'restaurants/merchant_table_receipt.html', context)


@login_required(login_url='/login/')
def update_order_item_status(request, slug, item_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    item = get_object_or_404(OrderItem, id=item_id, order__restaurant=restaurant)
    
    # ป้องกันไม่ให้แก้ไขอะไรต่อได้อีกหากรายการนั้นถูกยกเลิกไปแล้ว
    if item.status == 'cancelled':
        return redirect('merchant_table_status_manage', slug=restaurant.slug, table_number=item.order.table_number)

    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in ['pending', 'cooking', 'completed', 'cancelled']:
            item.status = new_status
            item.save()
            
    return redirect('merchant_table_status_manage', slug=restaurant.slug, table_number=item.order.table_number)


def cancel_order_item_customer(request, slug, item_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    item = get_object_or_404(OrderItem, id=item_id, order__restaurant=restaurant)
    table_number = item.order.table_number
    
    if item.status == 'pending':
        item.status = 'cancelled'
        item.save()
        
        order = item.order
        valid_items = order.items.exclude(status='cancelled')
        order.total_price = sum(i.price * i.quantity for i in valid_items)
        order.save()

    return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)


def update_order_item_quantity_customer(request, slug, item_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    item = get_object_or_404(OrderItem, id=item_id, order__restaurant=restaurant)
    table_number = item.order.table_number
    
    if item.status == 'pending' and request.method == 'POST':
        try:
            new_qty = int(request.POST.get('quantity', 1))
            if new_qty > 0:
                item.quantity = new_qty
                item.save()
            else:
                item.status = 'cancelled'
                item.save()
            
            order = item.order
            valid_items = order.items.exclude(status='cancelled')
            order.total_price = sum(i.price * i.quantity for i in valid_items)
            order.save()
        except ValueError:
            pass

    return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)


@login_required(login_url='/login/')
def merchant_table_status_manage(request, slug, table_number):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    
    if restaurant.user != request.user and not request.user.is_superuser:
        return render(request, 'restaurants/403.html', status=403)

    table_orders = restaurant.orders.filter(table_number=table_number, is_paid=False).order_by('created_at')

    context = {
        'restaurant': restaurant,
        'table_number': table_number,
        'table_orders': table_orders,
    }
    return render(request, 'restaurants/merchant_table_status.html', context)



@login_required(login_url='/login/')
def merchant_accounting_report(request, slug):
    """ หน้ารายงานบัญชี พร้อมบันทึกรายรับ-รายจ่าย และคำนวณกำไรสุทธิ """
    restaurant = get_object_or_404(Restaurant, slug=slug)
    
    if restaurant.user != request.user and not request.user.is_superuser:
        return render(request, 'restaurants/403.html', status=403)

    # จัดการการบันทึกข้อมูลรายรับ/รายจ่ายผ่าน POST
    if request.method == 'POST':
        if 'add_transaction' in request.POST:
            t_type = request.POST.get('transaction_type')
            title = request.POST.get('title')
            amount = request.POST.get('amount')
            date = request.POST.get('date') or timezone.localdate()
            note = request.POST.get('note', '')

            if title and amount:
                TransactionRecord.objects.create(
                    restaurant=restaurant,
                    transaction_type=t_type,
                    title=title,
                    amount=amount,
                    date=date,
                    note=note
                )
            return redirect('merchant_accounting_report', slug=restaurant.slug)

   # คำนวณรายรับจากการขายอาหาร/สินค้า
    paid_orders = restaurant.orders.filter(is_paid=True)
    sales_revenue = float(paid_orders.aggregate(total=Sum('total_price'))['total'] or 0)

    # ดึงรายการรายรับ-รายจ่ายอื่นๆ
    transactions = restaurant.transactions.all().order_by('-date', '-created_at')
    
    other_income = float(transactions.filter(transaction_type='income').aggregate(total=Sum('amount'))['total'] or 0)
    total_expenses = float(transactions.filter(transaction_type='expense').aggregate(total=Sum('amount'))['total'] or 0)

    # คำนวณกำไรสุทธิ (Net Profit) และปัดเศษ 2 ตำแหน่ง
    net_profit = round((sales_revenue + other_income) - total_expenses, 2)

    # ข้อมูลสำหรับกราฟและตาราง
    daily_sales = (
        paid_orders.annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(total=Sum('total_price'))
        .order_by('-date')[:10]
    )

    current_year = timezone.localdate().year
    monthly_sales = (
        paid_orders.filter(created_at__year=current_year)
        .annotate(month=ExtractMonth('created_at'))
        .values('month')
        .annotate(total=Sum('total_price'))
        .order_by('month')
    )

    months_data = [0] * 12
    for item in monthly_sales:
        m_index = item['month'] - 1
        months_data[m_index] = float(item['total'])

    top_selling_items = (
        OrderItem.objects.filter(order__restaurant=restaurant, order__is_paid=True)
        .exclude(status='cancelled')
        .values('menu_item__name')
        .annotate(
            total_qty=Sum('quantity'),
            total_revenue=Sum(F('price') * F('quantity'))
        )
        .order_by('-total_qty')[:10]
    )

    context = {
        'restaurant': restaurant,
        'sales_revenue': sales_revenue,
        'other_income': other_income,
        'total_expenses': total_expenses,
        'net_profit': net_profit,
        'transactions': transactions,
        'daily_sales': daily_sales,
        'months_data': months_data,
        'current_year': current_year,
        'top_selling_items': top_selling_items,
    }
    return render(request, 'restaurants/accounting_report.html', context)

@login_required(login_url='/login/')
def delete_transaction(request, slug, pk):
    """ ลบบันทึกรายรับ/รายจ่าย """
    restaurant = get_object_or_404(Restaurant, slug=slug)
    transaction = get_object_or_404(TransactionRecord, pk=pk, restaurant=restaurant)
    transaction.delete()
    return redirect('merchant_accounting_report', slug=restaurant.slug)