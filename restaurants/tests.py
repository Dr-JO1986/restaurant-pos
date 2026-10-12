from datetime import timedelta
from decimal import Decimal
from io import BytesIO
import tempfile

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
        self.assertContains(self.client.get(response.url), 'กำลังตรวจสอบสลิป')

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
