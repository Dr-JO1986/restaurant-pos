from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import (
    Category, MenuItem, OptionGroup, OptionItem, Order, OrderItem,
    Restaurant, RestaurantStaff, Table, WaiterCall,
)


class OnlinePickupOrderTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.media_dir.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)

        self.user = get_user_model().objects.create_user(username='merchant', password='test-pass')
        self.restaurant = Restaurant.objects.create(
            user=self.user,
            name='Test Restaurant',
            slug='test-restaurant',
            owner_name='Owner',
            phone='0812345678',
            is_active=True,
            bank_name='ธนาคารทดสอบ',
            bank_account_name='ร้านทดสอบ',
            bank_account_number='1234567890',
        )
        self.category = Category.objects.create(restaurant=self.restaurant, name='อาหาร')
        self.menu_item = MenuItem.objects.create(
            restaurant=self.restaurant,
            category=self.category,
            name='ข้าวผัด',
            price=Decimal('50.00'),
        )

    def create_slip_upload(self, filename='transfer.png'):
        image_data = BytesIO()
        Image.new('RGB', (1, 1), color='white').save(image_data, format='PNG')
        return SimpleUploadedFile(filename, image_data.getvalue(), content_type='image/png')

    def valid_order_data(self, **overrides):
        pickup_at = timezone.localtime(timezone.now() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
        data = {
            'customer_name': 'สมชาย',
            'customer_phone': '0812345678',
            'pickup_at': pickup_at,
            f'quantity_{self.menu_item.id}': '2',
            'payment_slip': self.create_slip_upload(),
        }
        data.update(overrides)
        return data

    def test_customer_can_submit_prepaid_pickup_order_with_slip_and_pickup_time(self):
        response = self.client.post(
            reverse('place_online_order', args=[self.restaurant.slug]),
            self.valid_order_data(),
        )

        order = Order.objects.get(restaurant=self.restaurant)
        self.assertRedirects(
            response,
            reverse('online_order_success', args=[self.restaurant.slug, order.public_token]),
        )
        self.assertEqual(order.order_type, 'pickup')
        self.assertEqual(order.customer_name, 'สมชาย')
        self.assertEqual(order.customer_phone, '0812345678')
        self.assertEqual(order.payment_status, 'pending')
        self.assertFalse(order.is_paid)
        self.assertFalse(order.is_collected)
        self.assertTrue(order.payment_slip)
        self.assertIsNotNone(order.pickup_at)
        self.assertEqual(order.total_price, Decimal('100.00'))
        self.assertEqual(OrderItem.objects.get(order=order).quantity, 2)
        success_response = self.client.get(response.url)
        self.assertContains(success_response, 'กำลังตรวจสอบสลิป')
        self.assertContains(
            success_response,
            reverse('online_order_receipt', args=[self.restaurant.slug, order.public_token]),
        )

    def test_online_order_adds_selected_option_price_and_saves_snapshot(self):
        group = OptionGroup.objects.create(restaurant=self.restaurant, name='ไข่')
        self.menu_item.option_groups.add(group)
        option = OptionItem.objects.create(group=group, name='ไข่ดาว', price=Decimal('15.00'))
        table_menu_response = self.client.get(
            reverse('customer_storefront_table', args=[self.restaurant.slug, 'A1']),
        )
        online_menu_response = self.client.get(reverse('online_storefront', args=[self.restaurant.slug]))
        self.assertContains(table_menu_response, 'data-option-price="15.00"')
        self.assertContains(online_menu_response, 'data-option-price="15.00"')

        response = self.client.post(
            reverse('place_online_order', args=[self.restaurant.slug]),
            self.valid_order_data(**{f'options_{self.menu_item.id}': str(option.id)}),
        )

        order = Order.objects.get(restaurant=self.restaurant)
        order_item = OrderItem.objects.get(order=order)
        self.assertRedirects(
            response,
            reverse('online_order_success', args=[self.restaurant.slug, order.public_token]),
        )
        self.assertEqual(order.total_price, Decimal('130.00'))
        self.assertEqual(order_item.price, Decimal('65.00'))
        self.assertEqual(
            order_item.selected_options,
            [{'id': option.id, 'name': 'ไข่ดาว', 'price': '15.00'}],
        )
        self.assertContains(self.client.get(response.url), 'ไข่ดาว')

    def test_online_order_rejects_options_not_attached_to_menu(self):
        group = OptionGroup.objects.create(restaurant=self.restaurant, name='ไข่')
        option = OptionItem.objects.create(group=group, name='ไข่ดาว', price=Decimal('15.00'))

        response = self.client.post(
            reverse('place_online_order', args=[self.restaurant.slug]),
            self.valid_order_data(**{f'options_{self.menu_item.id}': str(option.id)}),
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'ตัวเลือกเสริมไม่ถูกต้อง', status_code=400)
        self.assertFalse(Order.objects.exists())

    def test_table_order_includes_addon_price_and_kitchen_snapshot(self):
        group = OptionGroup.objects.create(restaurant=self.restaurant, name='ไข่')
        self.menu_item.option_groups.add(group)
        option = OptionItem.objects.create(group=group, name='ไข่ดาว', price=Decimal('15.00'))

        response = self.client.post(
            reverse('place_order_table', args=[self.restaurant.slug, 'A1']),
            {
                'customer_name': 'สมชาย',
                'menu_item_id': [str(self.menu_item.id)],
                'quantity': ['2'],
                f'options_{self.menu_item.id}': [str(option.id)],
            },
        )

        order = Order.objects.get(restaurant=self.restaurant)
        order_item = order.items.get()
        self.assertRedirects(
            response,
            reverse('customer_storefront_table', args=[self.restaurant.slug, 'A1']),
        )
        self.assertEqual(order.total_price, Decimal('130.00'))
        self.assertEqual(order_item.selected_options[0]['name'], 'ไข่ดาว')
        self.client.force_login(self.user)
        kitchen_response = self.client.get(reverse('merchant_orders', args=[self.restaurant.slug]))
        self.assertContains(kitchen_response, 'ไข่ดาว')

    def test_staff_pos_order_includes_selected_addon(self):
        group = OptionGroup.objects.create(restaurant=self.restaurant, name='ไข่')
        self.menu_item.option_groups.add(group)
        option = OptionItem.objects.create(group=group, name='ไข่ดาว', price=Decimal('15.00'))
        self.client.force_login(self.user)
        self.assertContains(
            self.client.get(reverse('staff_order_screen', args=[self.restaurant.slug])),
            'data-option-price="15.00"',
        )

        response = self.client.post(
            reverse('staff_order_screen', args=[self.restaurant.slug]),
            {
                'sale_type': 'counter',
                f'quantity_{self.menu_item.id}': '1',
                f'options_{self.menu_item.id}': str(option.id),
            },
        )

        order = Order.objects.get(restaurant=self.restaurant)
        self.assertRedirects(
            response,
            f"{reverse('staff_order_screen', args=[self.restaurant.slug])}?order={order.id}",
        )
        self.assertEqual(order.total_price, Decimal('65.00'))
        self.assertEqual(order.items.get().selected_options[0]['name'], 'ไข่ดาว')

    def test_owner_can_create_option_group_and_items_from_dashboard(self):
        self.client.force_login(self.user)
        dashboard_url = reverse('merchant_dashboard', args=[self.restaurant.slug])

        response = self.client.post(dashboard_url, {
            'add_option_group': '1',
            'name': 'ไข่และท็อปปิ้ง',
            'menu_items': [str(self.menu_item.id)],
        })
        self.assertRedirects(response, dashboard_url)
        group = OptionGroup.objects.get(restaurant=self.restaurant)
        self.assertIn(self.menu_item, group.menu_items.all())
        self.assertContains(self.client.get(dashboard_url), 'ไข่และท็อปปิ้ง')

        response = self.client.post(dashboard_url, {
            'add_option_item': '1',
            'group': str(group.id),
            'name': 'ไข่ดาว',
            'price': '15.00',
        })
        self.assertRedirects(response, dashboard_url)
        self.assertEqual(group.options.get().price, Decimal('15.00'))

    def test_order_requires_payment_slip_and_future_pickup_time(self):
        data = self.valid_order_data()
        del data['payment_slip']
        response = self.client.post(reverse('place_online_order', args=[self.restaurant.slug]), data)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'แนบสลิปโอนเงิน', status_code=400)
        self.assertFalse(Order.objects.exists())

        data = self.valid_order_data()
        data['pickup_at'] = timezone.localtime(timezone.now() - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(reverse('place_online_order', args=[self.restaurant.slug]), data)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'เวลาในอนาคต', status_code=400)
        self.assertFalse(Order.objects.exists())

    def test_invalid_slip_file_is_rejected(self):
        response = self.client.post(
            reverse('place_online_order', args=[self.restaurant.slug]),
            self.valid_order_data(payment_slip=SimpleUploadedFile(
                'transfer.txt',
                b'not an image',
                content_type='text/plain',
            )),
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'ไม่ใช่รูปภาพที่ถูกต้อง', status_code=400)
        self.assertFalse(Order.objects.exists())

    def test_online_storefront_requires_bank_details_before_accepting_orders(self):
        self.restaurant.bank_account_number = ''
        self.restaurant.save(update_fields=['bank_account_number'])

        response = self.client.get(reverse('online_storefront', args=[self.restaurant.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ยังไม่ได้ตั้งค่าบัญชีรับโอน')

    def test_online_storefront_filters_by_category_and_shows_responsive_filters(self):
        dessert_category = Category.objects.create(
            restaurant=self.restaurant,
            name='ของหวาน',
        )
        MenuItem.objects.create(
            restaurant=self.restaurant,
            category=dessert_category,
            name='เค้กช็อกโกแลต',
            price=Decimal('75.00'),
        )

        response = self.client.get(
            reverse('online_storefront', args=[self.restaurant.slug]),
            {'category': dessert_category.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'เค้กช็อกโกแลต')
        self.assertNotContains(response, 'ข้าวผัด')
        self.assertContains(response, 'id="category-filter-mobile"')
        self.assertContains(response, '<option value="">ดูทั้งหมด</option>', html=True)
        self.assertContains(response, f'<option value="{dessert_category.id}" selected>', html=False)
        self.assertContains(response, f'?category={dessert_category.id}')

    def test_owner_can_save_bank_transfer_details(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('merchant_dashboard', args=[self.restaurant.slug]),
            {
                'save_payment_details': '1',
                'bank_name': 'ธนาคารใหม่',
                'bank_account_name': 'ชื่อบัญชีใหม่',
                'bank_account_number': '999000',
            },
        )

        self.restaurant.refresh_from_db()
        self.assertRedirects(response, reverse('merchant_dashboard', args=[self.restaurant.slug]))
        self.assertEqual(self.restaurant.bank_name, 'ธนาคารใหม่')
        self.assertEqual(self.restaurant.bank_account_name, 'ชื่อบัญชีใหม่')
        self.assertEqual(self.restaurant.bank_account_number, '999000')

    def test_merchant_registration_creates_pending_account_and_blocks_pos(self):
        response = self.client.post(
            reverse('merchant_register'),
            {
                'username': 'pending-merchant',
                'email': 'merchant@example.com',
                'password1': 'StrongPass!7xyZ',
                'password2': 'StrongPass!7xyZ',
                'restaurant_name': 'ร้านทดลองภาษาไทย',
                'owner_name': 'เจ้าของร้าน',
                'phone': '0812345678',
                'address': 'กรุงเทพมหานคร',
                'restaurant_type': 'cafe',
            },
        )

        self.assertRedirects(response, reverse('restaurant_pending'))
        user = get_user_model().objects.get(username='pending-merchant')
        restaurant = Restaurant.objects.get(user=user)
        self.assertEqual(user.email, 'merchant@example.com')
        self.assertEqual(restaurant.address, 'กรุงเทพมหานคร')
        self.assertFalse(restaurant.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertContains(self.client.get(reverse('restaurant_pending')), 'อยู่ระหว่างรอตรวจสอบ')
        self.assertRedirects(
            self.client.get(reverse('staff_order_screen', args=[restaurant.slug])),
            reverse('restaurant_pending'),
        )
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse('online_storefront', args=[restaurant.slug])).status_code,
            404,
        )

    def test_login_for_unapproved_merchant_opens_pending_notice(self):
        merchant = get_user_model().objects.create_user(
            username='waiting-owner',
            password='MerchantPass!9xyZ',
        )
        Restaurant.objects.create(
            user=merchant,
            name='ร้านรออนุมัติ',
            slug='waiting-restaurant',
            owner_name='เจ้าของร้าน',
            phone='0812345678',
        )

        response = self.client.post(
            reverse('merchant_login'),
            {'username': 'waiting-owner', 'password': 'MerchantPass!9xyZ'},
        )

        self.assertRedirects(response, reverse('restaurant_pending'))
        self.assertContains(self.client.get(reverse('restaurant_pending')), 'ยังไม่สามารถเข้าใช้งานระบบ POS ได้')

    def test_admin_can_activate_and_deactivate_restaurants(self):
        approval_url = reverse('restaurant_approval_list')
        toggle_url = reverse('toggle_restaurant_active', args=[self.restaurant.id])

        self.client.force_login(self.user)
        self.assertEqual(self.client.get(approval_url).status_code, 302)
        self.assertEqual(self.client.post(toggle_url).status_code, 302)
        self.restaurant.refresh_from_db()
        self.assertTrue(self.restaurant.is_active)

        admin_user = get_user_model().objects.create_superuser(
            username='platform-admin',
            email='admin@example.com',
            password='StrongAdmin!8xyZ',
        )
        self.client.force_login(admin_user)
        self.assertContains(self.client.get(approval_url), 'Test Restaurant')
        response = self.client.post(toggle_url)
        self.assertRedirects(response, approval_url)
        self.restaurant.refresh_from_db()
        self.assertFalse(self.restaurant.is_active)

        self.client.post(toggle_url)
        self.restaurant.refresh_from_db()
        self.assertTrue(self.restaurant.is_active)

    def test_owner_can_edit_and_delete_categories_from_dashboard(self):
        self.client.force_login(self.user)
        dashboard_url = reverse('merchant_dashboard', args=[self.restaurant.slug])
        response = self.client.get(dashboard_url)
        self.assertContains(
            response,
            reverse('edit_category', args=[self.restaurant.slug, self.category.id]),
        )
        self.assertContains(
            response,
            reverse('delete_category', args=[self.restaurant.slug, self.category.id]),
        )

        response = self.client.post(dashboard_url, {
            'add_category': '1',
            'category_name': ' เครื่องดื่ม ',
        })
        self.assertRedirects(response, dashboard_url)
        self.assertTrue(Category.objects.filter(restaurant=self.restaurant, name='เครื่องดื่ม').exists())

        response = self.client.post(
            reverse('edit_category', args=[self.restaurant.slug, self.category.id]),
            {'name': 'อาหารจานหลัก'},
        )
        self.assertRedirects(response, dashboard_url)
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, 'อาหารจานหลัก')

        response = self.client.post(
            reverse('edit_category', args=[self.restaurant.slug, self.category.id]),
            {'name': ' '},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ฟิลด์นี้จำเป็น', html=False)

        category_to_delete = Category.objects.create(restaurant=self.restaurant, name='ของหวาน')
        MenuItem.objects.create(
            restaurant=self.restaurant,
            category=category_to_delete,
            name='เค้ก',
            price=Decimal('45.00'),
        )
        response = self.client.post(
            reverse('delete_category', args=[self.restaurant.slug, category_to_delete.id]),
        )
        self.assertRedirects(response, dashboard_url)
        self.assertFalse(Category.objects.filter(pk=category_to_delete.id).exists())
        self.assertFalse(MenuItem.objects.filter(name='เค้ก').exists())

    def test_dashboard_serves_menu_images_through_public_image_route(self):
        self.client.force_login(self.user)
        self.menu_item.image.save('menu.png', self.create_slip_upload(), save=True)

        dashboard_response = self.client.get(reverse('merchant_dashboard', args=[self.restaurant.slug]))
        image_url = reverse('menu_item_image', args=[self.restaurant.slug, self.menu_item.id])
        self.assertContains(dashboard_response, image_url)

        image_response = self.client.get(image_url)
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response['Content-Type'], 'image/png')
        image_response.close()

    def test_edit_menu_cancel_link_returns_to_merchant_dashboard(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('edit_menu_item', args=[self.restaurant.slug, self.menu_item.id]),
        )

        dashboard_url = reverse('merchant_dashboard', args=[self.restaurant.slug])
        self.assertContains(response, f'href="{dashboard_url}"')
        self.assertEqual(self.client.get(dashboard_url).status_code, 200)

    def test_customer_menu_pages_use_image_route(self):
        self.menu_item.image.save('menu.png', self.create_slip_upload(), save=True)
        image_url = reverse('menu_item_image', args=[self.restaurant.slug, self.menu_item.id])

        table_response = self.client.get(
            reverse('customer_storefront_table', args=[self.restaurant.slug, 'A1']),
        )
        online_response = self.client.get(
            reverse('online_storefront', args=[self.restaurant.slug]),
        )

        self.assertContains(table_response, image_url)
        self.assertContains(online_response, image_url)

    def test_customer_can_download_online_order_receipt_as_image(self):
        self.client.post(
            reverse('place_online_order', args=[self.restaurant.slug]),
            self.valid_order_data(),
        )
        order = Order.objects.get(restaurant=self.restaurant)

        response = self.client.get(
            reverse('online_order_receipt', args=[self.restaurant.slug, order.public_token]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/svg+xml; charset=utf-8')
        self.assertIn(f'receipt-order-{order.id}.svg', response['Content-Disposition'])
        self.assertContains(response, 'ใบรับสินค้า')
        self.assertContains(response, 'ข้าวผัด × 2')

    def test_pending_payment_is_reviewed_before_order_reaches_kitchen(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse('place_online_order', args=[self.restaurant.slug]),
            self.valid_order_data(),
        )
        order = Order.objects.get(restaurant=self.restaurant)

        response = self.client.get(reverse('merchant_orders', args=[self.restaurant.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ตรวจสอบสลิปออร์เดอร์ออนไลน์')
        self.assertContains(response, reverse('view_online_payment_slip', args=[self.restaurant.slug, order.id]))
        self.assertNotContains(response, reverse('checkout_online_order', args=[self.restaurant.slug, order.id]))
        self.assertFalse(order.is_paid)

        response = self.client.post(
            reverse('review_online_payment', args=[self.restaurant.slug, order.id]),
            {'action': 'confirm'},
        )
        order.refresh_from_db()
        self.assertRedirects(response, reverse('merchant_orders', args=[self.restaurant.slug]))
        self.assertEqual(order.payment_status, 'confirmed')
        self.assertTrue(order.is_paid)
        self.assertFalse(order.is_collected)

        response = self.client.get(reverse('merchant_orders', args=[self.restaurant.slug]))
        self.assertContains(
            response,
            reverse('checkout_online_order', args=[self.restaurant.slug, order.id]),
        )
        self.assertContains(response, 'ยืนยันว่าลูกค้ารับสินค้าแล้ว')
        self.assertContains(
            response,
            reverse('view_online_payment_slip', args=[self.restaurant.slug, order.id]),
        )

    def test_rejecting_payment_does_not_mark_order_paid_or_send_to_kitchen(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse('place_online_order', args=[self.restaurant.slug]),
            self.valid_order_data(),
        )
        order = Order.objects.get(restaurant=self.restaurant)

        self.client.post(
            reverse('review_online_payment', args=[self.restaurant.slug, order.id]),
            {'action': 'reject'},
        )
        order.refresh_from_db()

        self.assertEqual(order.payment_status, 'rejected')
        self.assertEqual(order.status, 'cancelled')
        self.assertFalse(order.is_paid)
        self.assertFalse(order.is_collected)

    def test_only_restaurant_owner_can_view_slip_or_review_payment(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse('place_online_order', args=[self.restaurant.slug]),
            self.valid_order_data(),
        )
        order = Order.objects.get(restaurant=self.restaurant)
        slip_url = reverse('view_online_payment_slip', args=[self.restaurant.slug, order.id])

        response = self.client.get(slip_url)
        self.assertEqual(response.status_code, 200)
        image_url = reverse('online_payment_slip_image', args=[self.restaurant.slug, order.id])
        self.assertContains(response, f'<img src="{image_url}"', html=False)

        response = self.client.get(image_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response['Content-Disposition'], 'inline')
        response.close()

        other_user = get_user_model().objects.create_user(username='other', password='test-pass')
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(slip_url).status_code, 403)
        self.assertEqual(self.client.get(image_url).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse('review_online_payment', args=[self.restaurant.slug, order.id]),
                {'action': 'confirm'},
            ).status_code,
            403,
        )

    def test_missing_online_payment_slip_returns_not_found_instead_of_server_error(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse('place_online_order', args=[self.restaurant.slug]),
            self.valid_order_data(),
        )
        order = Order.objects.get(restaurant=self.restaurant)
        order.payment_slip.storage.delete(order.payment_slip.name)

        response = self.client.get(
            reverse('online_payment_slip_image', args=[self.restaurant.slug, order.id]),
        )

        self.assertEqual(response.status_code, 404)

    def test_missing_menu_image_returns_not_found_instead_of_server_error(self):
        self.menu_item.image.save('menu.png', self.create_slip_upload(), save=True)
        image_name = self.menu_item.image.name
        self.menu_item.image.storage.delete(image_name)

        response = self.client.get(
            reverse('menu_item_image', args=[self.restaurant.slug, self.menu_item.id]),
        )

        self.assertEqual(response.status_code, 404)

    def test_bundled_menu_image_loads_when_media_root_is_separate(self):
        self.menu_item.image = 'menu_images/OIP.jpg'
        self.menu_item.save(update_fields=['image'])

        response = self.client.get(
            reverse('menu_item_image', args=[self.restaurant.slug, self.menu_item.id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        response.close()

    def test_table_storefront_renders_active_order_controls_for_mobile(self):
        order = Order.objects.create(
            restaurant=self.restaurant,
            table_number='A1',
            total_price=Decimal('100.00'),
        )
        order_item = OrderItem.objects.create(
            order=order,
            menu_item=self.menu_item,
            quantity=2,
            price=self.menu_item.price,
            status='pending',
        )

        response = self.client.get(
            reverse('customer_storefront_table', args=[self.restaurant.slug, 'A1']),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'รายการที่สั่งไปแล้วของโต๊ะ A1')
        self.assertContains(response, 'grid grid-cols-2 gap-2 w-full sm:w-auto')
        self.assertContains(response, 'ข้าวผัด')
        self.assertContains(response, 'จำนวน: 2')
        self.assertContains(response, 'รอดำเนินการ')
        self.assertContains(response, 'แก้ไขจำนวน')
        self.assertContains(
            response,
            reverse('cancel_order_item_customer', args=[self.restaurant.slug, order_item.id]),
        )

    @override_settings(CLOUDINARY_STORAGE_ENABLED=True)
    def test_menu_image_route_redirects_to_configured_cloud_storage(self):
        self.menu_item.image.save('uploaded-menu.png', self.create_slip_upload(), save=True)
        expected_cloud_url = self.menu_item.image.url

        response = self.client.get(
            reverse('menu_item_image', args=[self.restaurant.slug, self.menu_item.id]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], expected_cloud_url)

    @override_settings(CLOUDINARY_STORAGE_ENABLED=True)
    @patch('django.core.files.storage.FileSystemStorage.exists', return_value=False)
    def test_cloudinary_menu_image_falls_back_to_bundled_image(self, _exists):
        self.menu_item.image = 'menu_images/OIP.jpg'
        self.menu_item.save(update_fields=['image'])

        response = self.client.get(
            reverse('menu_item_image', args=[self.restaurant.slug, self.menu_item.id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        response.close()

    def test_paid_order_history_has_six_columns_and_links_to_receipt(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse('place_online_order', args=[self.restaurant.slug]),
            self.valid_order_data(),
        )
        online_order = Order.objects.get(restaurant=self.restaurant)
        online_order.payment_status = 'confirmed'
        online_order.is_paid = True
        online_order.save(update_fields=['payment_status', 'is_paid'])

        table_order = Order.objects.create(
            restaurant=self.restaurant,
            table_number='A1',
            order_type='table',
            customer_name='ลูกค้าหน้าร้าน',
            total_price=Decimal('50.00'),
            is_paid=True,
        )
        OrderItem.objects.create(
            order=table_order,
            menu_item=self.menu_item,
            quantity=1,
            price=self.menu_item.price,
        )

        response = self.client.get(reverse('merchant_orders', args=[self.restaurant.slug]))

        self.assertContains(response, 'สมชาย')
        self.assertContains(response, '฿100.00')
        self.assertContains(response, 'ลูกค้าหน้าร้าน')
        self.assertContains(response, 'A1')
        self.assertContains(
            response,
            f"{reverse('merchant_table_receipt', args=[self.restaurant.slug, table_order.table_number])}?order_id={table_order.id}",
        )
        self.assertContains(
            response,
            f"{reverse('merchant_table_receipt', args=[self.restaurant.slug, online_order.table_number])}?order_id={online_order.id}",
        )
        self.assertContains(response, '<th class="p-3 rounded-l-lg">รหัสออร์เดอร์</th>', html=True)
        self.assertEqual(response.content.count(b'<th class="p-3'), 6)

        online_receipt = self.client.get(
            reverse(
                'merchant_table_receipt',
                args=[self.restaurant.slug, online_order.table_number],
            ),
            {'order_id': online_order.id},
        )
        self.assertContains(online_receipt, 'ข้าวผัด')
        self.assertContains(online_receipt, 'สมชาย')
        self.assertContains(online_receipt, 'window.print()')
        self.assertContains(
            online_receipt,
            reverse('view_online_payment_slip', args=[self.restaurant.slug, online_order.id]),
        )

        table_receipt = self.client.get(
            reverse(
                'merchant_table_receipt',
                args=[self.restaurant.slug, table_order.table_number],
            ),
            {'order_id': table_order.id},
        )
        self.assertNotContains(table_receipt, 'เปิดดูสลิปโอนเงิน')

    def test_paid_order_history_filters_by_date_and_text_fields(self):
        self.client.force_login(self.user)
        older_date = timezone.localdate() - timedelta(days=2)
        selected_date = timezone.localdate() - timedelta(days=1)
        older_order = Order.objects.create(
            restaurant=self.restaurant,
            table_number='A1',
            customer_name='ลูกค้าคนแรก',
            total_price=Decimal('50.00'),
            is_paid=True,
        )
        selected_order = Order.objects.create(
            restaurant=self.restaurant,
            table_number='A2',
            customer_name='ลูกค้าคนที่สอง',
            total_price=Decimal('75.00'),
            is_paid=True,
        )
        older_order.created_at = timezone.make_aware(datetime.combine(older_date, datetime.min.time()))
        older_order.save(update_fields=['created_at'])
        selected_order.created_at = timezone.make_aware(datetime.combine(selected_date, datetime.min.time()))
        selected_order.save(update_fields=['created_at'])

        base_url = reverse('merchant_orders', args=[self.restaurant.slug])
        response = self.client.get(base_url, {'date': selected_date.isoformat()})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'name="q"')
        self.assertContains(response, f'type="date" name="date" value="{selected_date.isoformat()}"')
        self.assertContains(response, f'#{selected_order.id}')
        self.assertNotContains(response, f'#{older_order.id}')

        response = self.client.get(base_url, {'q': 'A1'})
        self.assertContains(response, f'#{older_order.id}')
        self.assertNotContains(response, f'#{selected_order.id}')

    def test_paid_order_history_shows_error_for_invalid_date_filter(self):
        self.client.force_login(self.user)
        paid_order = Order.objects.create(
            restaurant=self.restaurant,
            table_number='A1',
            total_price=Decimal('50.00'),
            is_paid=True,
        )

        response = self.client.get(
            reverse('merchant_orders', args=[self.restaurant.slug]),
            {'date': 'not-a-date'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'รูปแบบวันที่ไม่ถูกต้อง')
        self.assertNotContains(response, f'#{paid_order.id}')

    def test_owner_can_create_staff_account_and_staff_login_is_pos_only(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_manage', args=[self.restaurant.slug]))
        self.assertContains(
            response,
            f'href="{reverse("merchant_dashboard", args=[self.restaurant.slug])}"',
        )
        response = self.client.get(reverse('staff_order_screen', args=[self.restaurant.slug]))
        self.assertContains(
            response,
            f'href="{reverse("merchant_dashboard", args=[self.restaurant.slug])}"',
        )

        response = self.client.post(
            reverse('staff_manage', args=[self.restaurant.slug]),
            {
                'username': 'counter-staff',
                'email': 'counter@example.com',
                'password1': 'CoffeeCounter!78xz',
                'password2': 'CoffeeCounter!78xz',
            },
        )

        self.assertRedirects(response, reverse('staff_manage', args=[self.restaurant.slug]))
        staff_account = get_user_model().objects.get(username='counter-staff')
        staff_membership = RestaurantStaff.objects.get(user=staff_account)
        self.assertEqual(staff_membership.restaurant, self.restaurant)
        self.assertEqual(staff_membership.role, 'order')
        self.assertFalse(staff_account.is_staff)
        self.assertFalse(staff_account.is_superuser)

        reset_url = reverse(
            'staff_reset_password',
            args=[self.restaurant.slug, staff_membership.id],
        )
        response = self.client.get(reverse('staff_manage', args=[self.restaurant.slug]))
        self.assertContains(response, reset_url)
        response = self.client.post(reset_url, {
            'new_password1': 'NewCoffeePass!829z',
            'new_password2': 'NewCoffeePass!829y',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'The two password fields')
        response = self.client.post(reset_url, {
            'new_password1': 'NewCoffeePass!829z',
            'new_password2': 'NewCoffeePass!829z',
        })
        self.assertRedirects(response, reverse('staff_manage', args=[self.restaurant.slug]))
        staff_account.refresh_from_db()
        self.assertTrue(staff_account.check_password('NewCoffeePass!829z'))

        self.client.logout()
        response = self.client.post(
            reverse('merchant_login'),
            {'username': 'counter-staff', 'password': 'NewCoffeePass!829z'},
        )
        self.assertRedirects(response, reverse('staff_order_screen', args=[self.restaurant.slug]))
        self.assertEqual(
            self.client.get(reverse('staff_order_screen', args=[self.restaurant.slug])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse('merchant_dashboard', args=[self.restaurant.slug])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse('staff_manage', args=[self.restaurant.slug])).status_code,
            403,
        )
        self.assertEqual(self.client.get(reset_url).status_code, 403)

    def test_staff_dine_in_order_is_sent_to_kitchen_and_added_to_table_bill(self):
        table = Table.objects.create(restaurant=self.restaurant, name='A1')
        staff_user = get_user_model().objects.create_user(
            username='dine-in-staff',
            password='test-pass',
        )
        RestaurantStaff.objects.create(restaurant=self.restaurant, user=staff_user)
        self.client.force_login(staff_user)

        response = self.client.post(
            reverse('staff_order_screen', args=[self.restaurant.slug]),
            {
                'sale_type': 'table',
                'table_id': table.id,
                'customer_name': 'ลูกค้าหน้าร้าน',
                f'quantity_{self.menu_item.id}': '2',
            },
        )

        order = Order.objects.get(restaurant=self.restaurant)
        self.assertRedirects(
            response,
            f"{reverse('staff_order_screen', args=[self.restaurant.slug])}?order={order.id}",
        )
        self.assertEqual(order.order_type, 'table')
        self.assertEqual(order.table_number, table.name)
        self.assertEqual(order.created_by, staff_user)
        self.assertEqual(order.total_price, Decimal('100.00'))
        self.assertEqual(order.items.get().quantity, 2)
        self.assertContains(self.client.get(response.url), f'ออร์เดอร์ #{order.id} เข้าครัวแล้ว')

        staff_bill_url = reverse(
            'table_bill_summary',
            args=[self.restaurant.slug, table.name],
        )
        staff_bill_response = self.client.get(staff_bill_url)
        self.assertContains(
            staff_bill_response,
            f"{reverse('staff_order_screen', args=[self.restaurant.slug])}?table_id={table.id}",
        )
        pos_response = self.client.get(
            reverse('staff_order_screen', args=[self.restaurant.slug]),
            {'table_id': table.id},
        )
        self.assertEqual(pos_response.status_code, 200)
        self.assertContains(
            pos_response,
            f'<option value="{table.id}" selected>{table.name}</option>',
            html=True,
        )

        self.client.force_login(self.user)
        kitchen_response = self.client.get(reverse('merchant_orders', args=[self.restaurant.slug]))
        self.assertContains(kitchen_response, f'ออร์เดอร์ #{order.id}')
        bill_response = self.client.get(
            reverse('table_bill_summary', args=[self.restaurant.slug, table.name]),
        )
        self.assertContains(bill_response, 'ข้าวผัด')
        self.assertContains(bill_response, '฿100.00')

    def test_anonymous_customer_can_view_thai_table_bill_from_encoded_url(self):
        table_name = 'กระท่อมริมน้ำ 1'
        Table.objects.create(restaurant=self.restaurant, name=table_name)
        staff_user = get_user_model().objects.create_user(
            username='thai-table-staff',
            password='test-pass',
        )
        RestaurantStaff.objects.create(restaurant=self.restaurant, user=staff_user)
        Order.objects.create(
            restaurant=self.restaurant,
            created_by=staff_user,
            table_number=table_name,
            order_type='table',
            total_price=Decimal('100.00'),
        )
        Order.objects.create(
            restaurant=self.restaurant,
            table_number=table_name,
            order_type='table',
            total_price=Decimal('25.00'),
        )
        Order.objects.create(
            restaurant=self.restaurant,
            table_number=table_name,
            order_type='table',
            total_price=Decimal('900.00'),
            is_paid=True,
        )

        bill_url = reverse('table_bill_summary', args=[self.restaurant.slug, table_name])
        response = self.client.get(bill_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, table_name)
        self.assertContains(response, '฿125.00')
        self.assertEqual(response.context['grand_total'], Decimal('125.00'))
        self.assertEqual(len(response.context['unpaid_orders']), 2)

    def test_takeaway_staff_orders_have_individual_bills_and_can_be_checked_out(self):
        staff_user = get_user_model().objects.create_user(
            username='takeaway-staff',
            password='test-pass',
        )
        RestaurantStaff.objects.create(restaurant=self.restaurant, user=staff_user)
        self.client.force_login(staff_user)
        pos_url = reverse('staff_order_screen', args=[self.restaurant.slug])

        created_orders = []
        for _ in range(2):
            response = self.client.post(
                pos_url,
                {
                    'sale_type': 'counter',
                    f'quantity_{self.menu_item.id}': '1',
                },
            )
            self.assertEqual(response.status_code, 302)
            created_orders.append(Order.objects.latest('id'))

        first_order, second_order = created_orders
        self.assertEqual(first_order.order_type, 'counter')
        self.assertEqual(first_order.table_number, f'TAKEOUT-{first_order.id}')
        self.assertNotEqual(first_order.table_number, second_order.table_number)

        first_bill = self.client.get(
            reverse('table_bill_summary', args=[self.restaurant.slug, first_order.table_number]),
        )
        self.assertContains(first_bill, 'ออร์เดอร์กลับบ้าน')
        self.assertContains(first_bill, '฿50.00')
        self.assertEqual(len(first_bill.context['unpaid_orders']), 1)

        self.client.force_login(self.user)
        self.client.post(
            reverse('checkout_table_order', args=[self.restaurant.slug, first_order.table_number]),
        )
        first_order.refresh_from_db()
        second_order.refresh_from_db()
        self.assertTrue(first_order.is_paid)
        self.assertEqual(first_order.status, 'completed')
        self.assertFalse(second_order.is_paid)

    def test_disabling_staff_account_blocks_the_existing_session(self):
        staff_user = get_user_model().objects.create_user(
            username='disabled-staff',
            password='test-pass',
        )
        staff_membership = RestaurantStaff.objects.create(
            restaurant=self.restaurant,
            user=staff_user,
        )
        self.client.force_login(staff_user)

        self.client.force_login(self.user)
        self.client.post(
            reverse('staff_toggle_active', args=[self.restaurant.slug, staff_membership.id]),
        )

        self.client.force_login(staff_user)
        self.assertEqual(
            self.client.get(reverse('staff_order_screen', args=[self.restaurant.slug])).status_code,
            403,
        )
        self.client.logout()
        response = self.client.post(
            reverse('merchant_login'),
            {'username': 'disabled-staff', 'password': 'test-pass'},
        )
        self.assertContains(response, 'ถูกปิดใช้งาน')

    def test_staff_cannot_submit_order_for_another_restaurants_table(self):
        foreign_restaurant = Restaurant.objects.create(
            user=get_user_model().objects.create_user(username='other-owner', password='test-pass'),
            name='Other Restaurant',
            slug='other-restaurant',
            owner_name='Other Owner',
            phone='0811111111',
        )
        foreign_table = Table.objects.create(restaurant=foreign_restaurant, name='B1')
        staff_user = get_user_model().objects.create_user(username='secure-staff', password='test-pass')
        RestaurantStaff.objects.create(restaurant=self.restaurant, user=staff_user)
        self.client.force_login(staff_user)

        response = self.client.post(
            reverse('staff_order_screen', args=[self.restaurant.slug]),
            {
                'sale_type': 'table',
                'table_id': foreign_table.id,
                f'quantity_{self.menu_item.id}': '1',
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'ไม่พบโต๊ะที่เลือก', status_code=400)
        self.assertFalse(Order.objects.filter(restaurant=self.restaurant).exists())


class KitchenDisplayAndWaiterCallTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='kitchen-owner', password='test-pass')
        self.restaurant = Restaurant.objects.create(
            user=self.owner,
            name='Kitchen Test',
            slug='kitchen-test',
            owner_name='Owner',
            phone='0812345678',
            is_active=True,
        )
        self.category = Category.objects.create(restaurant=self.restaurant, name='อาหาร')
        self.menu_item = MenuItem.objects.create(
            restaurant=self.restaurant,
            category=self.category,
            name='ข้าวผัด',
            price=Decimal('50.00'),
        )

    def create_order(self):
        order = Order.objects.create(
            restaurant=self.restaurant,
            table_number='A1',
            order_type='table',
            status='pending',
        )
        item = OrderItem.objects.create(
            order=order,
            menu_item=self.menu_item,
            quantity=1,
            price=Decimal('50.00'),
            selected_options=[{'name': 'เพิ่มไข่ดาว'}],
        )
        return order, item

    def test_table_customer_can_call_waiter_and_pending_call_is_not_duplicated(self):
        Table.objects.create(restaurant=self.restaurant, name='A1')
        call_url = reverse('call_waiter', args=[self.restaurant.slug, 'A1'])
        response = self.client.post(call_url)

        self.assertRedirects(
            response,
            reverse('customer_storefront_table', args=[self.restaurant.slug, 'A1']),
        )
        call = WaiterCall.objects.get(restaurant=self.restaurant, table_number='A1')
        self.assertFalse(call.is_resolved)
        self.assertContains(self.client.get(response.url), 'เรียกพนักงานแล้ว')

        self.client.post(call_url)
        self.assertEqual(WaiterCall.objects.filter(restaurant=self.restaurant).count(), 1)

    def test_kitchen_staff_can_poll_and_owner_acknowledges_calls(self):
        staff_user = get_user_model().objects.create_user(username='kitchen-staff', password='test-pass')
        RestaurantStaff.objects.create(restaurant=self.restaurant, user=staff_user, role='kitchen')
        order, item = self.create_order()
        waiter_call = WaiterCall.objects.create(restaurant=self.restaurant, table_number='A1')
        self.client.force_login(staff_user)

        display = self.client.get(reverse('kitchen_display', args=[self.restaurant.slug]))
        self.assertEqual(display.status_code, 200)
        self.assertContains(display, 'Kitchen Display System')
        self.assertContains(display, 'ทุก 3 วินาที')

        updates = self.client.get(reverse('kitchen_live_updates', args=[self.restaurant.slug]))
        self.assertContains(updates, f'data-order-id="{order.id}"')
        self.assertContains(updates, 'เพิ่มไข่ดาว')
        self.assertNotContains(updates, 'เรียกพนักงาน')
        self.assertEqual(
            self.client.get(reverse('owner_waiter_calls_live', args=[self.restaurant.slug])).status_code,
            403,
        )

        response = self.client.post(
            reverse('kitchen_update_item_status', args=[self.restaurant.slug, item.id]),
            {'status': 'cooking'},
        )
        self.assertRedirects(response, reverse('kitchen_display', args=[self.restaurant.slug]))
        item.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(item.status, 'cooking')
        self.assertEqual(order.status, 'cooking')

        self.client.force_login(self.owner)
        owner_calls = self.client.get(reverse('owner_waiter_calls_live', args=[self.restaurant.slug]))
        self.assertContains(owner_calls, 'โต๊ะ')
        self.assertContains(owner_calls, 'A1')
        response = self.client.post(
            reverse('resolve_waiter_call', args=[self.restaurant.slug, waiter_call.id]),
        )
        self.assertRedirects(response, reverse('merchant_dashboard', args=[self.restaurant.slug]))
        waiter_call = WaiterCall.objects.get(restaurant=self.restaurant, table_number='A1')
        self.assertTrue(waiter_call.is_resolved)
        self.assertIsNotNone(waiter_call.resolved_at)

    def test_kitchen_routes_require_authorized_user_and_post_status_changes(self):
        order, item = self.create_order()
        display_url = reverse('kitchen_display', args=[self.restaurant.slug])
        update_url = reverse('kitchen_update_order_status', args=[self.restaurant.slug, order.id])

        anonymous_response = self.client.get(display_url)
        self.assertEqual(anonymous_response.status_code, 302)

        unrelated_user = get_user_model().objects.create_user(username='unrelated', password='test-pass')
        self.client.force_login(unrelated_user)
        forbidden_response = self.client.get(display_url)
        self.assertEqual(forbidden_response.status_code, 403)
        self.assertEqual(self.client.get(reverse('kitchen_live_updates', args=[self.restaurant.slug])).status_code, 403)

        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(display_url).status_code, 403)
        self.assertEqual(self.client.get(update_url).status_code, 403)
        self.client.logout()
        kitchen_user = get_user_model().objects.create_user(username='only-kitchen', password='test-pass')
        RestaurantStaff.objects.create(
            restaurant=self.restaurant,
            user=kitchen_user,
            role='kitchen',
        )
        self.client.force_login(kitchen_user)
        self.assertEqual(self.client.get(update_url).status_code, 405)
        self.client.post(update_url, {'status': 'cooking'})
        order.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(order.status, 'cooking')
        self.assertEqual(item.status, 'cooking')

    def test_order_staff_and_kitchen_staff_are_limited_to_their_own_screens(self):
        order_staff = get_user_model().objects.create_user(username='order-staff', password='test-pass')
        RestaurantStaff.objects.create(restaurant=self.restaurant, user=order_staff, role='order')
        self.client.force_login(order_staff)
        self.assertEqual(self.client.get(reverse('staff_order_screen', args=[self.restaurant.slug])).status_code, 200)
        self.assertEqual(self.client.get(reverse('kitchen_display', args=[self.restaurant.slug])).status_code, 403)

        kitchen_user = get_user_model().objects.create_user(username='restricted-kitchen', password='test-pass')
        RestaurantStaff.objects.create(restaurant=self.restaurant, user=kitchen_user, role='kitchen')
        self.client.force_login(kitchen_user)
        self.assertEqual(self.client.get(reverse('kitchen_display', args=[self.restaurant.slug])).status_code, 200)
        self.assertEqual(self.client.get(reverse('staff_order_screen', args=[self.restaurant.slug])).status_code, 403)

    def test_kitchen_login_redirects_to_kitchen_screen(self):
        kitchen_user = get_user_model().objects.create_user(username='login-kitchen', password='test-pass')
        RestaurantStaff.objects.create(restaurant=self.restaurant, user=kitchen_user, role='kitchen')

        response = self.client.post(
            reverse('merchant_login'),
            {'username': 'login-kitchen', 'password': 'test-pass'},
        )

        self.assertRedirects(response, reverse('kitchen_display', args=[self.restaurant.slug]))

    def test_owner_can_create_kitchen_login_and_dashboard_shows_waiter_calls(self):
        Table.objects.create(restaurant=self.restaurant, name='A1')
        waiter_call = WaiterCall.objects.create(restaurant=self.restaurant, table_number='A1')
        self.client.force_login(self.owner)

        manage_url = reverse('staff_manage', args=[self.restaurant.slug])
        self.assertContains(self.client.get(manage_url), 'พ่อครัว / หน้าจอครัว')
        response = self.client.post(manage_url, {
            'username': 'owner-created-cook',
            'email': 'cook@example.com',
            'password1': 'KitchenPass!829z',
            'password2': 'KitchenPass!829z',
            'role': 'kitchen',
        })

        self.assertRedirects(response, manage_url)
        cook = get_user_model().objects.get(username='owner-created-cook')
        membership = RestaurantStaff.objects.get(user=cook)
        self.assertEqual(membership.role, 'kitchen')
        self.assertTrue(cook.check_password('KitchenPass!829z'))

        dashboard_url = reverse('merchant_dashboard', args=[self.restaurant.slug])
        self.assertContains(
            self.client.get(dashboard_url),
            reverse('owner_waiter_calls_live', args=[self.restaurant.slug]),
        )
        live_calls = self.client.get(reverse('owner_waiter_calls_live', args=[self.restaurant.slug]))
        self.assertContains(live_calls, f'data-call-id="{waiter_call.id}"')

        self.client.force_login(cook)
        self.assertEqual(self.client.get(reverse('merchant_dashboard', args=[self.restaurant.slug])).status_code, 403)
        self.assertEqual(self.client.get(reverse('merchant_orders', args=[self.restaurant.slug])).status_code, 403)
