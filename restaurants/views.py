from functools import wraps
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib.auth.decorators import user_passes_test
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.utils.html import escape
from django.utils.dateparse import parse_date, parse_datetime
from pathlib import Path
from .models import (
    Restaurant, RestaurantStaff, MenuItem, Category, OptionGroup, OptionItem, Order, OrderItem, Table, TransactionRecord,
    WaiterCall, ContactInquiry, validate_payment_slip,
)
from .forms import (
    MenuItemForm, CategoryForm, OptionGroupForm, OptionItemForm,
    StaffAccountCreationForm, StaffPasswordResetForm,
    MerchantRegistrationForm,
)
from django.utils import timezone
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.db.models.functions import ExtractYear, ExtractMonth, ExtractDay
from django.db.models.functions import TruncDate
from django.db.models import Sum, F
from django.views.decorators.http import require_POST


def _can_manage_restaurant(request, restaurant):
    return restaurant.user == request.user or request.user.is_superuser


def _can_place_staff_order(request, restaurant):
    if _can_manage_restaurant(request, restaurant):
        return True
    return RestaurantStaff.objects.filter(
        restaurant=restaurant,
        user=request.user,
        is_active=True,
    ).exists()


def _can_access_kitchen(request, restaurant):
    if request.user.is_superuser:
        return True
    if restaurant.user == request.user:
        return True
    return RestaurantStaff.objects.filter(
        restaurant=restaurant,
        user=request.user,
        is_active=True,
    ).exists()


def kitchen_staff_required(view_func):
    @wraps(view_func)
    @login_required(login_url='/login/')
    def _wrapped(request, *args, **kwargs):
        slug = kwargs.get('slug')
        restaurant = get_object_or_404(Restaurant, slug=slug) if slug else None
        if restaurant is None or not _can_access_kitchen(request, restaurant):
            messages.error(request, 'บัญชีของคุณไม่มีสิทธิ์เข้าหน้าจอครัว กรุณาเข้าสู่ระบบด้วยบัญชีพนักงานที่ได้รับอนุญาต')
            return redirect('merchant_login')
        return view_func(request, *args, **kwargs)
    return _wrapped


def _get_selected_options(request, menu_item):
    option_ids = request.POST.getlist(f'options_{menu_item.id}')
    if len(option_ids) != len(set(option_ids)) or any(not option_id.isdigit() for option_id in option_ids):
        return [], 'ตัวเลือกเสริมไม่ถูกต้อง กรุณาเลือกใหม่'
    if not option_ids:
        return [], None

    options = list(
        OptionItem.objects.filter(
            pk__in=option_ids,
            group__restaurant=menu_item.restaurant,
            group__in=menu_item.option_groups.all(),
        ).select_related('group')
    )
    if len(options) != len(option_ids):
        return [], 'ตัวเลือกเสริมไม่ถูกต้องหรือไม่พร้อมให้บริการ กรุณาเลือกใหม่'
    return options, None


def _option_snapshot(options):
    return [
        {'id': option.id, 'name': option.name, 'price': str(option.price)}
        for option in options
    ]


def _option_total(options):
    return sum((option.price for option in options), start=0)


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


def merchant_register(request):
    if request.user.is_authenticated:
        restaurant = Restaurant.objects.filter(user=request.user).first()
        if restaurant:
            return redirect(
                'merchant_dashboard' if restaurant.is_active else 'restaurant_pending',
                **({'slug': restaurant.slug} if restaurant.is_active else {}),
            )

    form = MerchantRegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            user = form.save()
        login(request, user)
        return redirect('restaurant_pending')

    return render(request, 'restaurants/merchant_register.html', {'form': form})


@login_required(login_url='/login/')
def restaurant_pending(request):
    restaurant = Restaurant.objects.filter(user=request.user).first()
    if restaurant:
        if restaurant.is_active:
            return redirect('merchant_dashboard', slug=restaurant.slug)
    else:
        staff_membership = RestaurantStaff.objects.select_related('restaurant').filter(
            user=request.user,
        ).first()
        if not staff_membership:
            return redirect('merchant_login')
        restaurant = staff_membership.restaurant
        if restaurant.is_active and staff_membership.is_active:
            return redirect('staff_order_screen', slug=restaurant.slug)

    return render(request, 'restaurants/restaurant_pending.html', {'restaurant': restaurant})


@user_passes_test(lambda user: user.is_superuser, login_url='/admin/login/')
def restaurant_approval_list(request):
    restaurants = Restaurant.objects.select_related('user').order_by('is_active', 'name')
    return render(
        request,
        'restaurants/restaurant_approval_list.html',
        {'restaurants': restaurants},
    )


@user_passes_test(lambda user: user.is_superuser, login_url='/admin/login/')
@require_POST
def toggle_restaurant_active(request, restaurant_id):
    restaurant = get_object_or_404(Restaurant, pk=restaurant_id)
    restaurant.is_active = not restaurant.is_active
    restaurant.save(update_fields=['is_active'])
    status_text = 'อนุมัติและเปิดใช้งาน' if restaurant.is_active else 'ระงับการใช้งาน'
    messages.success(request, f'{status_text}ร้าน "{restaurant.name}" แล้ว')
    return redirect('restaurant_approval_list')
    
@login_required(login_url='/login/')
def merchant_dashboard(request, slug):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    
    if restaurant.user != request.user and not request.user.is_superuser:
        return render(request, 'restaurants/403.html', status=403)

    menu_items = restaurant.menu_items.all()
    categories = restaurant.categories.all()

    menu_form = MenuItemForm()
    menu_form.fields['category'].queryset = categories
    menu_form.fields['option_groups'].queryset = restaurant.option_groups.all()
    option_group_form = OptionGroupForm()
    option_group_form.fields['menu_items'].queryset = menu_items
    option_item_form = OptionItemForm()
    option_item_form.fields['group'].queryset = restaurant.option_groups.all()

    if request.method == 'POST':
        if 'save_payment_details' in request.POST:
            bank_name = request.POST.get('bank_name', '').strip()
            account_name = request.POST.get('bank_account_name', '').strip()
            account_number = request.POST.get('bank_account_number', '').strip()
            if len(bank_name) > 100 or len(account_name) > 100 or len(account_number) > 50:
                messages.error(request, 'ข้อมูลบัญชีธนาคารยาวเกินกำหนด กรุณาตรวจสอบอีกครั้ง')
            else:
                restaurant.bank_name = bank_name
                restaurant.bank_account_name = account_name
                restaurant.bank_account_number = account_number
                restaurant.save(update_fields=['bank_name', 'bank_account_name', 'bank_account_number'])
                messages.success(request, 'บันทึกข้อมูลบัญชีรับโอนแล้ว')
            return redirect('merchant_dashboard', slug=restaurant.slug)
        elif 'add_menu' in request.POST:
            menu_form = MenuItemForm(request.POST, request.FILES)
            menu_form.fields['category'].queryset = categories
            menu_form.fields['option_groups'].queryset = restaurant.option_groups.all()
            if menu_form.is_valid():
                menu_item = menu_form.save(commit=False)
                menu_item.restaurant = restaurant
                menu_item.save()
                menu_form.save_m2m()
                return redirect('merchant_dashboard', slug=restaurant.slug)
        
        elif 'add_category' in request.POST:
            cat_name = request.POST.get('category_name', '').strip()
            if cat_name and len(cat_name) <= 150:
                Category.objects.create(restaurant=restaurant, name=cat_name)
                return redirect('merchant_dashboard', slug=restaurant.slug)
        elif 'add_option_group' in request.POST:
            option_group_form = OptionGroupForm(request.POST)
            option_group_form.fields['menu_items'].queryset = menu_items
            if option_group_form.is_valid():
                option_group = option_group_form.save(commit=False)
                option_group.restaurant = restaurant
                option_group.save()
                option_group.menu_items.set(option_group_form.cleaned_data['menu_items'])
                messages.success(request, f'สร้างกลุ่มตัวเลือก "{option_group.name}" แล้ว')
                return redirect('merchant_dashboard', slug=restaurant.slug)
        elif 'add_option_item' in request.POST:
            option_item_form = OptionItemForm(request.POST)
            option_item_form.fields['group'].queryset = restaurant.option_groups.all()
            if option_item_form.is_valid():
                option_item_form.save()
                messages.success(request, 'เพิ่มตัวเลือกเสริมแล้ว')
                return redirect('merchant_dashboard', slug=restaurant.slug)
        elif 'delete_option_item' in request.POST:
            option_item = get_object_or_404(
                OptionItem,
                pk=request.POST.get('delete_option_item'),
                group__restaurant=restaurant,
            )
            option_item.delete()
            messages.success(request, 'ลบตัวเลือกเสริมแล้ว')
            return redirect('merchant_dashboard', slug=restaurant.slug)
        elif 'delete_option_group' in request.POST:
            option_group = get_object_or_404(
                OptionGroup,
                pk=request.POST.get('delete_option_group'),
                restaurant=restaurant,
            )
            option_group.delete()
            messages.success(request, 'ลบกลุ่มตัวเลือกแล้ว')
            return redirect('merchant_dashboard', slug=restaurant.slug)

    context = {
        'restaurant': restaurant,
        'menu_items': menu_items,
        'categories': categories,
        'menu_form': menu_form,
        'option_group_form': option_group_form,
        'option_item_form': option_item_form,
        'option_groups': restaurant.option_groups.prefetch_related('options', 'menu_items'),
    }
    return render(request, 'restaurants/dashboard.html', context)


@login_required(login_url='/login/')
def staff_order_screen(request, slug):
    restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
    if not _can_place_staff_order(request, restaurant):
        return HttpResponseForbidden()

    categories = restaurant.categories.all()
    menu_items = list(
        restaurant.menu_items.filter(is_available=True)
        .select_related('category')
        .prefetch_related('option_groups__options')
        .order_by('category__name', 'name')
    )
    tables = restaurant.tables.all().order_by('name')
    error = ''
    recent_order = None
    selected_table_id = request.GET.get('table_id', '')
    if not selected_table_id.isdigit() or not tables.filter(pk=selected_table_id).exists():
        selected_table_id = ''
    selected_sale_type = 'table' if selected_table_id else ''
    recent_order_id = request.GET.get('order', '')
    if recent_order_id.isdigit():
        recent_order = restaurant.orders.filter(
            pk=recent_order_id,
            created_by=request.user,
        ).first()

    if request.method == 'POST':
        sale_type = request.POST.get('sale_type', '')
        customer_name = request.POST.get('customer_name', '').strip()
        table = None
        selected_items = []

        if sale_type not in {'table', 'counter'}:
            error = 'กรุณาเลือกประเภทการขาย'
        elif sale_type == 'table':
            table_id = request.POST.get('table_id', '')
            if not table_id.isdigit():
                error = 'กรุณาเลือกโต๊ะ'
            else:
                table = restaurant.tables.filter(pk=table_id).first()
                if table is None:
                    error = 'ไม่พบโต๊ะที่เลือก กรุณาเลือกโต๊ะของร้านนี้'
        if not error and len(customer_name) > 100:
            error = 'ชื่อผู้สั่งต้องมีความยาวไม่เกิน 100 ตัวอักษร'

        if not error:
            for menu_item in menu_items:
                raw_quantity = request.POST.get(f'quantity_{menu_item.id}', '0')
                try:
                    quantity = int(raw_quantity)
                except (TypeError, ValueError):
                    error = 'จำนวนสินค้าไม่ถูกต้อง กรุณาตรวจสอบอีกครั้ง'
                    break
                if quantity < 0 or quantity > 99:
                    error = 'จำนวนสินค้าต้องอยู่ระหว่าง 0 ถึง 99 ชิ้น'
                    break
                if quantity:
                    options, option_error = _get_selected_options(request, menu_item)
                    if option_error:
                        error = option_error
                        break
                    selected_items.append((menu_item, quantity, options))

            if not error and not selected_items:
                error = 'กรุณาเลือกเมนูอย่างน้อย 1 รายการ'

        if not error:
            with transaction.atomic():
                order = Order.objects.create(
                    restaurant=restaurant,
                    created_by=request.user,
                    table_number=table.name if table else 'COUNTER',
                    order_type='table' if table else 'counter',
                    customer_name=customer_name or None,
                    status='pending',
                    is_paid=False,
                    total_price=0,
                )
                total_price = 0
                for menu_item, quantity, options in selected_items:
                    unit_price = menu_item.price + _option_total(options)
                    OrderItem.objects.create(
                        order=order,
                        menu_item=menu_item,
                        quantity=quantity,
                        price=unit_price,
                        selected_options=_option_snapshot(options),
                        status='pending',
                    )
                    total_price += unit_price * quantity
                if not table:
                    order.table_number = f'TAKEOUT-{order.id}'
                order.total_price = total_price
                order.save(update_fields=['table_number', 'total_price'])

            messages.success(request, f'ส่งออร์เดอร์ #{order.id} เข้าครัวแล้ว')
            return redirect(f"{reverse('staff_order_screen', args=[restaurant.slug])}?order={order.id}")

        for menu_item in menu_items:
            menu_item.order_quantity = request.POST.get(f'quantity_{menu_item.id}', '0')
            menu_item.order_options = request.POST.getlist(f'options_{menu_item.id}')
        selected_sale_type = sale_type if sale_type in {'table', 'counter'} else ''
        selected_table_id = request.POST.get('table_id', '')
        return render(request, 'restaurants/staff_order_screen.html', {
            'restaurant': restaurant,
            'can_manage_restaurant': _can_manage_restaurant(request, restaurant),
            'categories': categories,
            'menu_items': menu_items,
            'tables': tables,
            'error': error,
            'customer_name': customer_name,
            'selected_sale_type': selected_sale_type,
            'selected_table_id': selected_table_id,
            'can_place_table_order': tables.exists(),
            'recent_order': recent_order,
        }, status=400)

    return render(request, 'restaurants/staff_order_screen.html', {
        'restaurant': restaurant,
        'can_manage_restaurant': _can_manage_restaurant(request, restaurant),
        'categories': categories,
        'menu_items': menu_items,
        'tables': tables,
        'can_place_table_order': tables.exists(),
        'selected_sale_type': selected_sale_type,
        'selected_table_id': selected_table_id,
        'recent_order': recent_order,
    })


@login_required(login_url='/login/')
def staff_manage(request, slug):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    if not _can_manage_restaurant(request, restaurant):
        return HttpResponseForbidden()

    form = StaffAccountCreationForm()
    if request.method == 'POST':
        form = StaffAccountCreationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                RestaurantStaff.objects.create(restaurant=restaurant, user=user)
            messages.success(request, f'สร้างบัญชีพนักงาน "{user.get_username()}" แล้ว')
            return redirect('staff_manage', slug=restaurant.slug)

    return render(request, 'restaurants/staff_manage.html', {
        'restaurant': restaurant,
        'form': form,
        'staff_members': restaurant.staff_members.select_related('user'),
    })


@login_required(login_url='/login/')
@require_POST
def staff_toggle_active(request, slug, staff_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    if not _can_manage_restaurant(request, restaurant):
        return HttpResponseForbidden()

    staff = get_object_or_404(RestaurantStaff, pk=staff_id, restaurant=restaurant)
    staff.is_active = not staff.is_active
    staff.save(update_fields=['is_active'])
    status_text = 'เปิดใช้งาน' if staff.is_active else 'ปิดใช้งาน'
    messages.success(request, f'{status_text}บัญชีพนักงาน "{staff.user.get_username()}" แล้ว')
    return redirect('staff_manage', slug=restaurant.slug)


@login_required(login_url='/login/')
def staff_reset_password(request, slug, staff_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    if not _can_manage_restaurant(request, restaurant):
        return HttpResponseForbidden()

    staff = get_object_or_404(
        RestaurantStaff.objects.select_related('user'),
        pk=staff_id,
        restaurant=restaurant,
    )
    form = StaffPasswordResetForm(
        staff.user,
        request.POST if request.method == 'POST' else None,
    )
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'ตั้งรหัสผ่านใหม่ให้บัญชี "{staff.user.get_username()}" แล้ว')
        return redirect('staff_manage', slug=restaurant.slug)

    return render(request, 'restaurants/staff_reset_password.html', {
        'restaurant': restaurant,
        'staff': staff,
        'form': form,
    })


@login_required(login_url='/login/')
def edit_menu_item(request, slug, pk):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    menu_item = get_object_or_404(MenuItem, pk=pk, restaurant=restaurant)
    
    if request.method == 'POST':
        form = MenuItemForm(request.POST, request.FILES, instance=menu_item)
        form.fields['category'].queryset = restaurant.categories.all()
        form.fields['option_groups'].queryset = restaurant.option_groups.all()
        if form.is_valid():
            form.save()
            return redirect('merchant_dashboard', slug=restaurant.slug)
    else:
        form = MenuItemForm(instance=menu_item)
        form.fields['category'].queryset = restaurant.categories.all()
        form.fields['option_groups'].queryset = restaurant.option_groups.all()

    context = {'restaurant': restaurant, 'form': form, 'menu_item': menu_item}
    return render(request, 'restaurants/edit_menu.html', context)

@login_required(login_url='/login/')
def delete_menu_item(request, slug, pk):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    menu_item = get_object_or_404(MenuItem, pk=pk, restaurant=restaurant)
    menu_item.delete()
    return redirect('merchant_dashboard', slug=restaurant.slug)


def menu_item_image(request, slug, pk):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    menu_item = get_object_or_404(MenuItem, pk=pk, restaurant=restaurant)
    if not menu_item.image:
        raise Http404

    bundled_media_root = (Path(settings.BASE_DIR) / 'media').resolve()
    bundled_image = (bundled_media_root / menu_item.image.name).resolve()
    try:
        bundled_image.relative_to(bundled_media_root)
    except ValueError as error:
        raise Http404("ไม่พบไฟล์รูปภาพเมนูในพื้นที่จัดเก็บ") from error

    if settings.CLOUDINARY_STORAGE_ENABLED:
        if menu_item.image.storage.exists(menu_item.image.name):
            return redirect(menu_item.image.url)
        local_media_root = Path(settings.MEDIA_ROOT).resolve()
        local_image = (local_media_root / menu_item.image.name).resolve()
        try:
            local_image.relative_to(local_media_root)
        except ValueError as error:
            raise Http404("ไม่พบไฟล์รูปภาพเมนูในพื้นที่จัดเก็บ") from error

        fallback_image = next(
            (path for path in (local_image, bundled_image) if path.is_file()),
            None,
        )
        if fallback_image is None:
            raise Http404("ไม่พบไฟล์รูปภาพเมนูใน Cloudinary หรือพื้นที่จัดเก็บเดิม")

        image_file = fallback_image.open('rb')
        content_type = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.webp': 'image/webp',
        }.get(Path(menu_item.image.name).suffix.lower(), 'application/octet-stream')
        return FileResponse(image_file, content_type=content_type)

    content_type = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.webp': 'image/webp',
    }.get(Path(menu_item.image.name).suffix.lower(), 'application/octet-stream')

    if menu_item.image.storage.exists(menu_item.image.name):
        try:
            image_file = menu_item.image.open('rb')
        except FileNotFoundError as error:
            raise Http404("ไม่พบไฟล์รูปภาพเมนูในพื้นที่จัดเก็บ") from error
    else:
        fallback_image = next(
            (path for path in (bundled_image, (Path(settings.MEDIA_ROOT) / menu_item.image.name).resolve())
             if path.is_file()),
            None,
        )
        if fallback_image is None:
            raise Http404("ไม่พบไฟล์รูปภาพเมนูในพื้นที่จัดเก็บ")
        image_file = fallback_image.open('rb')

    response = FileResponse(image_file, content_type=content_type)
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required(login_url='/login/')
def edit_category(request, slug, pk):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    if restaurant.user != request.user and not request.user.is_superuser:
        return HttpResponseForbidden()
    category = get_object_or_404(Category, pk=pk, restaurant=restaurant)

    form = CategoryForm(request.POST or None, instance=category)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'บันทึกชื่อหมวดหมู่แล้ว')
        return redirect('merchant_dashboard', slug=restaurant.slug)

    context = {'restaurant': restaurant, 'category': category, 'form': form}
    return render(request, 'restaurants/edit_category.html', context)

@login_required(login_url='/login/')
@require_POST
def delete_category(request, slug, pk):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    if restaurant.user != request.user and not request.user.is_superuser:
        return HttpResponseForbidden()
    category = get_object_or_404(Category, pk=pk, restaurant=restaurant)
    category_name = category.name
    category.delete()
    messages.success(request, f'ลบหมวดหมู่ "{category_name}" แล้ว')
    return redirect('merchant_dashboard', slug=restaurant.slug)


def customer_storefront(request, slug, table_number):
    restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
    table_exists = Table.objects.filter(restaurant=restaurant, name=table_number).exists()
    categories = restaurant.categories.all()
    menu_items = restaurant.menu_items.filter(is_available=True).prefetch_related(
        'option_groups__options',
    )
    
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
        'table_exists': table_exists,
        'active_orders': active_orders,
        'has_pending_waiter_call': restaurant.waiter_calls.filter(
            table_number=table_number,
            is_resolved=False,
        ).exists(),
    }
    return render(request, 'restaurants/storefront.html', context)


@require_POST
def call_waiter(request, slug, table_number):
    restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
    table_number = table_number.strip()
    if not table_number or len(table_number) > 50:
        return HttpResponseBadRequest('ข้อมูลโต๊ะไม่ถูกต้อง')
    if not Table.objects.filter(restaurant=restaurant, name=table_number).exists():
        raise Http404('ไม่พบโต๊ะนี้ในร้าน')

    pending_call = restaurant.waiter_calls.filter(
        table_number=table_number,
        is_resolved=False,
    ).first()
    if pending_call:
        messages.info(request, 'พนักงานได้รับการแจ้งเรียกจากโต๊ะนี้แล้ว กำลังไปให้บริการ')
    else:
        WaiterCall.objects.create(restaurant=restaurant, table_number=table_number)
        messages.success(request, 'เรียกพนักงานแล้ว กรุณารอสักครู่')
    return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)


def online_storefront(request, slug):
    restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
    categories = restaurant.categories.all()
    selected_category_id = request.GET.get('category')
    selected_category = (
        categories.filter(pk=selected_category_id).first()
        if selected_category_id
        else None
    )
    menu_items = restaurant.menu_items.filter(is_available=True).select_related(
        'category',
    ).prefetch_related('option_groups__options')
    if selected_category:
        menu_items = menu_items.filter(category=selected_category)

    return render(request, 'restaurants/online_storefront.html', {
        'restaurant': restaurant,
        'categories': categories,
        'menu_items': menu_items,
        'selected_category': selected_category,
        'can_accept_online_orders': bool(
            restaurant.bank_name
            and restaurant.bank_account_name
            and restaurant.bank_account_number
        ),
    })


@require_POST
def place_online_order(request, slug):
    restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
    categories = restaurant.categories.all()
    selected_category_id = request.POST.get('category')
    selected_category = (
        categories.filter(pk=selected_category_id).first()
        if selected_category_id
        else None
    )
    menu_items = list(restaurant.menu_items.filter(is_available=True).select_related('category'))
    customer_name = request.POST.get('customer_name', '').strip()
    customer_phone = request.POST.get('customer_phone', '').strip()
    pickup_at_value = request.POST.get('pickup_at', '').strip()
    payment_slip = request.FILES.get('payment_slip')
    selected_items = []
    pickup_at = None
    error = None

    if not (restaurant.bank_name and restaurant.bank_account_name and restaurant.bank_account_number):
        error = 'ร้านยังไม่ได้ตั้งค่าบัญชีรับโอน กรุณาติดต่อร้านก่อนสั่งซื้อ'
    elif not customer_name or len(customer_name) > 100:
        error = 'กรุณาระบุชื่อผู้สั่ง (ไม่เกิน 100 ตัวอักษร)'
    elif len(customer_phone) < 7 or len(customer_phone) > 20:
        error = 'กรุณาระบุเบอร์โทรศัพท์ให้ถูกต้อง'
    elif not payment_slip:
        error = 'กรุณาแนบสลิปโอนเงินก่อนยืนยันออร์เดอร์'
    elif not pickup_at_value:
        error = 'กรุณาเลือกวันที่และเวลาที่ต้องการรับสินค้า'
    else:
        pickup_at = parse_datetime(pickup_at_value)
        if pickup_at is None:
            error = 'รูปแบบวันที่และเวลารับสินค้าไม่ถูกต้อง'
        else:
            if timezone.is_naive(pickup_at):
                pickup_at = timezone.make_aware(pickup_at, timezone.get_current_timezone())
            if pickup_at <= timezone.now():
                error = 'เวลารับสินค้าต้องเป็นเวลาในอนาคต'

        if not error:
            try:
                validate_payment_slip(payment_slip)
            except ValidationError as validation_error:
                error = validation_error.messages[0]

        if not error:
            for menu_item in menu_items:
                raw_quantity = request.POST.get(f'quantity_{menu_item.id}', '0')
                try:
                    quantity = int(raw_quantity)
                except (TypeError, ValueError):
                    error = 'จำนวนสินค้าไม่ถูกต้อง กรุณาตรวจสอบอีกครั้ง'
                    break
                if quantity < 0 or quantity > 99:
                    error = 'จำนวนสินค้าต้องอยู่ระหว่าง 0 ถึง 99 ชิ้น'
                    break
                if quantity:
                    options, option_error = _get_selected_options(request, menu_item)
                    if option_error:
                        error = option_error
                        break
                    selected_items.append((menu_item, quantity, options))

            if not error and not selected_items:
                error = 'กรุณาเลือกเมนูอย่างน้อย 1 รายการ'

    if error:
        for menu_item in menu_items:
            menu_item.order_quantity = request.POST.get(f'quantity_{menu_item.id}', '0')
            menu_item.order_options = request.POST.getlist(f'options_{menu_item.id}')
        displayed_menu_items = menu_items
        if selected_category:
            displayed_menu_items = [
                menu_item for menu_item in menu_items
                if menu_item.category_id == selected_category.id
            ]
        return render(request, 'restaurants/online_storefront.html', {
            'restaurant': restaurant,
            'categories': categories,
            'menu_items': displayed_menu_items,
            'selected_category': selected_category,
            'can_accept_online_orders': bool(
                restaurant.bank_name
                and restaurant.bank_account_name
                and restaurant.bank_account_number
            ),
            'error': error,
            'customer_name': customer_name,
            'customer_phone': customer_phone,
            'pickup_at': pickup_at_value,
        }, status=400)

    with transaction.atomic():
        order = Order.objects.create(
            restaurant=restaurant,
            table_number='ONLINE',
            order_type='pickup',
            customer_name=customer_name,
            customer_phone=customer_phone,
            pickup_at=pickup_at,
            payment_slip=payment_slip,
            payment_status='pending',
            status='pending',
            total_price=0,
        )
        order.table_number = f'ONLINE-{order.id}'
        total_price = 0
        for menu_item, quantity, options in selected_items:
            unit_price = menu_item.price + _option_total(options)
            OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                quantity=quantity,
                price=unit_price,
                selected_options=_option_snapshot(options),
                status='pending',
            )
            total_price += unit_price * quantity
        order.total_price = total_price
        order.save(update_fields=['table_number', 'total_price'])

    return redirect('online_order_success', slug=restaurant.slug, token=order.public_token)


def online_order_success(request, slug, token):
    restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
    order = get_object_or_404(
        Order,
        restaurant=restaurant,
        public_token=token,
        order_type='pickup',
    )
    return render(request, 'restaurants/online_order_success.html', {
        'restaurant': restaurant,
        'order': order,
    })


def online_order_receipt(request, slug, token):
    restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
    order = get_object_or_404(
        Order.objects.prefetch_related('items__menu_item'),
        restaurant=restaurant,
        public_token=token,
        order_type='pickup',
    )
    line_height = 50
    height = 350 + order.items.count() * line_height
    item_rows = []
    for index, item in enumerate(order.items.all()):
        row_y = 220 + index * line_height
        item_rows.append(
            f'<text x="40" y="{row_y}" font-size="18">'
            f'{escape(item.menu_item.name)} × {item.quantity}</text>'
            f'<text x="560" y="{row_y}" text-anchor="end" font-size="18">'
            f'฿{item.price:,.2f}</text>'
        )
        if item.selected_options:
            option_names = escape(', '.join(option['name'] for option in item.selected_options))
            item_rows.append(
                f'<text x="55" y="{row_y + 18}" font-size="13" fill="#4f46e5">'
                f'เพิ่ม: {option_names}</text>'
            )
    item_rows = ''.join(item_rows)
    pickup_time = (
        timezone.localtime(order.pickup_at).strftime('%d/%m/%Y %H:%M')
        if order.pickup_at else '-'
    )
    total_y = 240 + order.items.count() * line_height
    receipt_svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="600" height="{height}" viewBox="0 0 600 {height}">
<rect width="100%" height="100%" fill="#f3f4f6"/>
<rect x="16" y="16" width="568" height="{height - 32}" rx="20" fill="#fff"/>
<text x="40" y="68" font-family="Prompt, sans-serif" font-size="26" font-weight="700">{escape(restaurant.name)}</text>
<text x="40" y="104" font-family="Prompt, sans-serif" font-size="19">ใบรับสินค้า · ออร์เดอร์ #{order.id}</text>
<text x="40" y="140" font-family="Prompt, sans-serif" font-size="17">ผู้สั่ง: {escape(order.customer_name or '-')}</text>
<text x="40" y="174" font-family="Prompt, sans-serif" font-size="17">เวลารับสินค้า: {pickup_time}</text>
<line x1="40" y1="192" x2="560" y2="192" stroke="#d1d5db"/>
{item_rows}
<line x1="40" y1="{total_y - 24}" x2="560" y2="{total_y - 24}" stroke="#d1d5db"/>
<text x="40" y="{total_y + 10}" font-family="Prompt, sans-serif" font-size="20" font-weight="700">ยอดรวม</text>
<text x="560" y="{total_y + 10}" text-anchor="end" font-family="Prompt, sans-serif" font-size="20" font-weight="700">฿{order.total_price:,.2f}</text>
<text x="40" y="{total_y + 48}" font-family="Prompt, sans-serif" font-size="15" fill="#4b5563">กรุณาแสดงใบรับสินค้านี้เมื่อมารับสินค้า</text>
</svg>'''
    response = HttpResponse(receipt_svg, content_type='image/svg+xml; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="receipt-order-{order.id}.svg"'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required(login_url='/login/')
def view_online_payment_slip(request, slug, order_id):
    order = _get_online_payment_slip_order(request, slug, order_id)
    return render(request, 'restaurants/online_payment_slip.html', {
        'restaurant': order.restaurant,
        'order': order,
    })


def _get_online_payment_slip_order(request, slug, order_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    if restaurant.user != request.user and not request.user.is_superuser:
        raise PermissionDenied

    order = get_object_or_404(
        Order,
        id=order_id,
        restaurant=restaurant,
        order_type='pickup',
    )
    if not order.payment_slip:
        raise Http404("ออร์เดอร์นี้ไม่มีไฟล์สลิปโอนเงิน")
    if not order.payment_slip.storage.exists(order.payment_slip.name):
        raise Http404("ไม่พบไฟล์สลิปโอนเงินในพื้นที่จัดเก็บ")
    return order


@login_required(login_url='/login/')
def online_payment_slip_image(request, slug, order_id):
    order = _get_online_payment_slip_order(request, slug, order_id)
    content_type = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.webp': 'image/webp',
    }.get(Path(order.payment_slip.name).suffix.lower(), 'application/octet-stream')
    try:
        slip_file = order.payment_slip.open('rb')
    except FileNotFoundError as error:
        raise Http404("ไม่พบไฟล์สลิปโอนเงินในพื้นที่จัดเก็บ") from error
    response = FileResponse(slip_file, content_type=content_type)
    response['Content-Disposition'] = 'inline'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required(login_url='/login/')
@require_POST
def review_online_payment(request, slug, order_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    if restaurant.user != request.user and not request.user.is_superuser:
        return HttpResponseForbidden()

    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            id=order_id,
            restaurant=restaurant,
            order_type='pickup',
            payment_status='pending',
            is_paid=False,
        )
        action = request.POST.get('action')
        if action == 'confirm':
            order.payment_status = 'confirmed'
            order.is_paid = True
            order.save(update_fields=['payment_status', 'is_paid'])
            messages.success(request, f'ยืนยันยอดโอนของออร์เดอร์ #{order.id} แล้ว ออร์เดอร์จะแสดงในรายการครัว')
        elif action == 'reject':
            order.payment_status = 'rejected'
            order.status = 'cancelled'
            order.save(update_fields=['payment_status', 'status'])
            messages.warning(request, f'ปฏิเสธสลิปของออร์เดอร์ #{order.id} แล้ว')
        else:
            messages.error(request, 'ไม่พบคำสั่งตรวจสอบสลิปที่ถูกต้อง')

    return redirect('merchant_orders', slug=restaurant.slug)


def place_order(request, slug, table_number):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    
    if request.method == 'POST':
        customer_name = request.POST.get('customer_name', '').strip()
        menu_item_ids = request.POST.getlist('menu_item_id')
        quantities = request.POST.getlist('quantity')
        if len(customer_name) > 100 or len(menu_item_ids) != len(quantities):
            messages.error(request, 'ข้อมูลออร์เดอร์ไม่ถูกต้อง กรุณาตรวจสอบแล้วลองใหม่')
            return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)

        selected_items = []
        for item_id, raw_quantity in zip(menu_item_ids, quantities):
            try:
                quantity = int(raw_quantity or 0)
            except (TypeError, ValueError):
                messages.error(request, 'จำนวนสินค้าไม่ถูกต้อง กรุณาตรวจสอบอีกครั้ง')
                return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)
            if quantity < 0 or quantity > 99:
                messages.error(request, 'จำนวนสินค้าต้องอยู่ระหว่าง 0 ถึง 99 ชิ้น')
                return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)
            menu_item = MenuItem.objects.filter(
                id=item_id,
                restaurant=restaurant,
                is_available=True,
            ).first()
            if menu_item is None:
                messages.error(request, 'ไม่พบเมนูที่เลือกหรือเมนูนี้ไม่พร้อมให้บริการ')
                return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)
            if quantity:
                options, option_error = _get_selected_options(request, menu_item)
                if option_error:
                    messages.error(request, option_error)
                    return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)
                selected_items.append((menu_item, quantity, options))

        if not selected_items:
            messages.error(request, 'กรุณาเลือกเมนูอย่างน้อย 1 รายการ')
            return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)

        with transaction.atomic():
            order = Order.objects.create(
                restaurant=restaurant,
                table_number=table_number,
                customer_name=customer_name,
                status='pending',
                is_paid=False,
                total_price=0
            )

            total_price = 0
            for menu_item, quantity, options in selected_items:
                unit_price = menu_item.price + _option_total(options)
                OrderItem.objects.create(
                    order=order,
                    menu_item=menu_item,
                    quantity=quantity,
                    price=unit_price,
                    selected_options=_option_snapshot(options),
                    status='pending'
                )
                total_price += unit_price * quantity

            order.total_price = total_price
            order.save(update_fields=['total_price'])
        
        return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)
        
    return redirect('customer_storefront_table', slug=restaurant.slug, table_number=table_number)


def order_success(request, slug, table_number, order_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    order = get_object_or_404(Order, id=order_id, restaurant=restaurant)
    context = {'restaurant': restaurant, 'order': order, 'table_number': table_number}
    return render(request, 'restaurants/order_success.html', context)


@kitchen_staff_required
def kitchen_display_view(request, slug):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    context = _kitchen_display_context(restaurant)
    return render(request, 'restaurants/kitchen_display.html', context)


def _kitchen_display_context(restaurant):
    orders = (
        restaurant.orders.filter(
            status__in=['pending', 'cooking'],
            items__status__in=['pending', 'cooking'],
        )
        .filter(
            Q(order_type__in=['table', 'counter'])
            | Q(order_type='pickup', payment_status='confirmed')
        )
        .prefetch_related(
            Prefetch(
                'items',
                queryset=OrderItem.objects.select_related('menu_item').order_by('id'),
            ),
        )
        .distinct()
        .order_by('created_at')
    )

    for order in orders:
        order.items_list = list(order.items.all())

    return {
        'restaurant': restaurant,
        'orders': orders,
        'waiter_calls': restaurant.waiter_calls.filter(is_resolved=False),
    }


@kitchen_staff_required
def kitchen_live_updates(request, slug):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    response = render(
        request,
        'restaurants/kitchen_live_updates.html',
        _kitchen_display_context(restaurant),
    )
    response['Cache-Control'] = 'no-store'
    return response


@kitchen_staff_required
@require_POST
def kitchen_update_order_status(request, slug, order_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    order = get_object_or_404(Order, id=order_id, restaurant=restaurant)
    new_status = request.POST.get('status')

    if (
        new_status in dict(Order.STATUS_CHOICES)
        and (order.order_type != 'pickup' or order.payment_status == 'confirmed')
    ):
        order.status = new_status
        if new_status == 'completed':
            order.items.exclude(status='cancelled').update(status='completed')
        elif new_status == 'cooking':
            order.items.exclude(status='cancelled').update(status='cooking')
        elif new_status == 'pending':
            order.items.exclude(status='cancelled').update(status='pending')
        elif new_status == 'cancelled':
            order.items.exclude(status='cancelled').update(status='cancelled')
        order.save(update_fields=['status'])
        messages.success(request, f'อัปเดตสถานะออร์เดอร์ #{order.id} เป็น "{order.get_status_display()}" แล้ว')

    return redirect('kitchen_display', slug=restaurant.slug)


@kitchen_staff_required
@require_POST
def kitchen_update_item_status(request, slug, item_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    item = get_object_or_404(
        OrderItem.objects.select_related('order'),
        id=item_id,
        order__restaurant=restaurant,
    )
    new_status = request.POST.get('status')
    allowed_statuses = {'pending', 'cooking', 'completed'}
    if item.status != 'cancelled' and new_status in allowed_statuses and (
        item.order.order_type != 'pickup' or item.order.payment_status == 'confirmed'
    ):
        item.status = new_status
        item.save(update_fields=['status'])
        active_items = item.order.items.exclude(status='cancelled')
        if active_items.exists() and not active_items.exclude(status='completed').exists():
            item.order.status = 'completed'
        elif active_items.filter(status='cooking').exists():
            item.order.status = 'cooking'
        else:
            item.order.status = 'pending'
        item.order.save(update_fields=['status'])
    return redirect('kitchen_display', slug=restaurant.slug)


@kitchen_staff_required
@require_POST
def resolve_waiter_call(request, slug, call_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    waiter_call = get_object_or_404(
        WaiterCall,
        id=call_id,
        restaurant=restaurant,
        is_resolved=False,
    )
    waiter_call.is_resolved = True
    waiter_call.resolved_at = timezone.now()
    waiter_call.save(update_fields=['is_resolved', 'resolved_at'])
    return redirect('kitchen_display', slug=restaurant.slug)


@login_required(login_url='/login/')
def merchant_orders(request, slug):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    
    if restaurant.user != request.user and not request.user.is_superuser:
        return render(request, 'restaurants/403.html', status=403)

    payment_review_orders = restaurant.orders.filter(
        order_type='pickup',
        payment_status='pending',
        is_paid=False,
    ).order_by('created_at')
    unpaid_orders = restaurant.orders.filter(
        Q(order_type='table', is_paid=False)
        | Q(order_type='counter', is_paid=False)
        | Q(order_type='pickup', payment_status='confirmed', is_collected=False)
    ).order_by('-created_at')
    
    active_tables_dict = {}
    for order in unpaid_orders:
        group_key = (order.order_type, order.table_number)
        if group_key not in active_tables_dict:
            active_tables_dict[group_key] = {
                'table_number': order.table_number,
                'order_type': order.order_type,
                'orders': [],
                'total_amount': 0,
                'order_count': 0,
            }
        active_tables_dict[group_key]['orders'].append(order)
        active_tables_dict[group_key]['total_amount'] += order.total_price
        active_tables_dict[group_key]['order_count'] += 1

    active_tables = list(active_tables_dict.values())

    # 2. ประวัติบิลที่ชำระเงินแล้ว พร้อมระบบค้นหา และ Pagination (15 รายการต่อหน้า)
    paid_orders_list = restaurant.orders.filter(is_paid=True).prefetch_related(
        'items__menu_item',
    ).order_by('-created_at')
    
    search_query = request.GET.get('q', '').strip()
    search_date = request.GET.get('date', '').strip()
    if search_query:
        clean_id = search_query.replace('#', '')
        if clean_id.isdigit():
            paid_orders_list = paid_orders_list.filter(
                Q(id=clean_id)
                | Q(table_number__icontains=search_query)
                | Q(customer_name__icontains=search_query)
            )
        else:
            paid_orders_list = paid_orders_list.filter(
                Q(table_number__icontains=search_query) |
                Q(customer_name__icontains=search_query)
            )

    search_date_error = ''
    if search_date:
        parsed_search_date = parse_date(search_date)
        if parsed_search_date is None:
            search_date_error = 'รูปแบบวันที่ไม่ถูกต้อง กรุณาเลือกวันที่จากปฏิทิน'
            paid_orders_list = paid_orders_list.none()
        else:
            paid_orders_list = paid_orders_list.filter(created_at__date=parsed_search_date)

    daily_total_sales = sum(o.total_price for o in paid_orders_list)

    paginator = Paginator(paid_orders_list, 5)
    page_number = request.GET.get('page')
    paid_orders_today = paginator.get_page(page_number)

    context = {
        'restaurant': restaurant,
        'active_tables': active_tables,
        'payment_review_orders': payment_review_orders,
        'paid_orders_today': paid_orders_today,
        'daily_total_sales': daily_total_sales,
        'search_query': search_query,
        'search_date': search_date,
        'search_date_error': search_date_error,
    }
    return render(request, 'restaurants/merchant_orders.html', context)


@login_required(login_url='/login/')
def update_order_status(request, slug, order_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    order = get_object_or_404(Order, id=order_id, restaurant=restaurant)

    if order.order_type == 'pickup' and restaurant.user != request.user and not request.user.is_superuser:
        return HttpResponseForbidden()

    if not _can_access_kitchen(request, restaurant):
        messages.error(request, 'บัญชีของคุณไม่มีสิทธิ์เปลี่ยนสถานะแอออร์เดอร์')
        return redirect('merchant_login')

    if request.method == 'POST':
        new_status = request.POST.get('status')
        if (
            new_status in dict(Order.STATUS_CHOICES)
            and (order.order_type != 'pickup' or order.payment_status == 'confirmed')
        ):
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
    if restaurant.user != request.user and not request.user.is_superuser:
        return HttpResponseForbidden()

    orders = restaurant.orders.filter(
        table_number=table_number,
        is_paid=False,
        order_type__in=['table', 'counter'],
    )
    
    if request.method == 'POST':
        orders.update(is_paid=True, status='completed')
        return redirect('merchant_orders', slug=restaurant.slug)
    return redirect('merchant_orders', slug=restaurant.slug)


@login_required(login_url='/login/')
@require_POST
def checkout_online_order(request, slug, order_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    if restaurant.user != request.user and not request.user.is_superuser:
        return HttpResponseForbidden()

    order = get_object_or_404(
        Order,
        id=order_id,
        restaurant=restaurant,
        order_type='pickup',
        payment_status='confirmed',
        is_collected=False,
    )
    order.is_collected = True
    order.is_paid = True
    order.status = 'completed'
    order.save(update_fields=['is_collected', 'is_paid', 'status'])
    return redirect('merchant_orders', slug=restaurant.slug)


def table_bill_summary(request, slug, table_number):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    unpaid_orders = restaurant.orders.filter(table_number=table_number, is_paid=False).order_by('created_at')
    grand_total = sum(order.total_price for order in unpaid_orders)
    is_staff_order_user = request.user.is_authenticated and RestaurantStaff.objects.filter(
        restaurant=restaurant,
        user=request.user,
        is_active=True,
    ).exists()
    table = restaurant.tables.filter(name=table_number).first()

    context = {
        'restaurant': restaurant,
        'table_number': table_number,
        'unpaid_orders': unpaid_orders,
        'grand_total': grand_total,
        'is_counter_order': unpaid_orders.filter(order_type='counter').exists(),
        'is_staff_order_user': is_staff_order_user,
        'table_id': table.id if table else None,
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
        table_orders = restaurant.orders.filter(
            id=order_id,
            restaurant=restaurant,
            table_number=table_number,
        ).prefetch_related('items__menu_item')
    else:
        table_orders = restaurant.orders.filter(
            table_number=table_number,
            is_paid=False,
        ).prefetch_related('items__menu_item').order_by('created_at')

    table_orders = list(table_orders)
    first_order = table_orders[0] if table_orders else None
    for order in table_orders:
        for item in order.items.all():
            item.line_total = item.price * item.quantity

    grand_total = sum(order.total_price for order in table_orders)

    context = {
        'restaurant': restaurant,
        'table_number': table_number,
        'table_orders': table_orders,
        'first_order': first_order,
        'grand_total': grand_total,
    }
    return render(request, 'restaurants/merchant_table_receipt.html', context)


@login_required(login_url='/login/')
def update_order_item_status(request, slug, item_id):
    restaurant = get_object_or_404(Restaurant, slug=slug)
    if not _can_access_kitchen(request, restaurant):
        messages.error(request, 'บัญชีของคุณไม่มีสิทธิ์เปลี่ยนสถานะรายการอาหาร')
        return redirect('merchant_login')

    item = get_object_or_404(OrderItem, id=item_id, order__restaurant=restaurant)

    if item.order.order_type == 'pickup' and item.order.payment_status != 'confirmed':
        return redirect('merchant_orders', slug=restaurant.slug)

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

    table_orders = restaurant.orders.filter(table_number=table_number).filter(
        Q(order_type='table', is_paid=False)
        | Q(order_type='counter', is_paid=False)
        | Q(order_type='pickup', payment_status='confirmed', is_collected=False)
    ).order_by('created_at')

    context = {
        'restaurant': restaurant,
        'table_number': table_number,
        'table_orders': table_orders,
        'is_pickup': table_orders.filter(order_type='pickup').exists(),
        'is_counter': table_orders.filter(order_type='counter').exists(),
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