from datetime import timedelta
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

from .models import Category, MenuItem, Order, OrderItem, Restaurant


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
        self.assertEqual(response['Content-Type'], 'image/png')
        response.close()

        other_user = get_user_model().objects.create_user(username='other', password='test-pass')
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(slip_url).status_code, 403)
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
            reverse('view_online_payment_slip', args=[self.restaurant.slug, order.id]),
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
