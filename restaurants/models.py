from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils import timezone
from pathlib import Path
from PIL import Image, UnidentifiedImageError
import uuid


def validate_payment_slip(uploaded_file):
    if uploaded_file.size > 5 * 1024 * 1024:
        raise ValidationError("ไฟล์สลิปต้องมีขนาดไม่เกิน 5 MB")

    try:
        with Image.open(uploaded_file) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValidationError("รองรับไฟล์สลิป JPG, PNG หรือ WEBP เท่านั้น")
            if image.width * image.height > 25_000_000:
                raise ValidationError("ขนาดภาพสลิปใหญ่เกินไป")
            expected_extensions = {
                'JPEG': {'.jpg', '.jpeg'},
                'PNG': {'.png'},
                'WEBP': {'.webp'},
            }
            if Path(uploaded_file.name).suffix.lower() not in expected_extensions[image.format]:
                raise ValidationError("นามสกุลไฟล์ไม่ตรงกับชนิดรูปภาพ")
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise ValidationError("ไฟล์สลิปไม่ใช่รูปภาพที่ถูกต้อง") from error
    finally:
        uploaded_file.seek(0)


def payment_slip_upload_path(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"payment_slips/{instance.restaurant.slug}/{uuid.uuid4().hex}{extension}"

# เลือกประเภทธุรกิจที่ร้านค้าสามารถเลือกได้ตอนสมัครหรือตั้งค่า
RESTAURANT_TYPES = [
    ('general', 'ร้านอาหารทั่วไป / ตามสั่ง'),
    ('cafe', 'คาเฟ่ & เครื่องดื่ม'),
    ('bakery', 'ร้านเค้ก & เบเกอรี่'),
    ('fastfood', 'ฟาสต์ฟู้ด / ของทานเล่น'),
    ('somtum', 'ร้านส้มตำ / อาหารอีสาน'),
    ('noodle', 'ร้านก๋วยเตี๋ยว'),
]

class Restaurant(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, verbose_name="บัญชีผู้ใช้งาน")
    name = models.CharField(max_length=255, verbose_name="ชื่อร้านอาหาร")
    slug = models.SlugField(unique=True, max_length=255, verbose_name="ลิงก์ร้าน (Slug)")
    restaurant_type = models.CharField(max_length=50, choices=RESTAURANT_TYPES, default='general', verbose_name="ประเภทกิจการ/ร้านค้า")
    owner_name = models.CharField(max_length=255, verbose_name="ชื่อเจ้าของร้าน")
    phone = models.CharField(max_length=20, verbose_name="เบอร์โทรศัพท์")
    address = models.TextField(blank=True, null=True, verbose_name="ที่อยู่ร้าน")
    bank_name = models.CharField(max_length=100, blank=True, verbose_name="ธนาคารรับโอน")
    bank_account_name = models.CharField(max_length=100, blank=True, verbose_name="ชื่อบัญชี")
    bank_account_number = models.CharField(max_length=50, blank=True, verbose_name="เลขบัญชี")
    is_active = models.BooleanField(default=False, verbose_name="สถานะเปิดใช้งาน")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="วันที่สมัคร")

    def __str__(self):
        return f"{self.name} ({self.get_restaurant_type_display()})"
class Category(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='categories', verbose_name="ร้านอาหาร")
    name = models.CharField(max_length=150, verbose_name="ชื่อหมวดหมู่ (เช่น อาหารจานเดียว, เครื่องดื่ม)")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.restaurant.name})"

class MenuItem(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='menu_items', verbose_name="ร้านอาหาร")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='items', verbose_name="หมวดหมู่")
    option_groups = models.ManyToManyField(
        'OptionGroup',
        blank=True,
        related_name='menu_items',
        verbose_name="กลุ่มตัวเลือกเสริม",
    )
    name = models.CharField(max_length=200, verbose_name="ชื่อเมนูอาหาร")
    price = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="ราคา (บาท)")
    description = models.TextField(blank=True, null=True, verbose_name="คำอธิบายเมนู")
    is_available = models.BooleanField(default=True, verbose_name="เปิดขาย / พร้อมเสิร์ฟ")
    image = models.ImageField(upload_to='menu_images/', blank=True, null=True, verbose_name="รูปภาพอาหาร")

    def __str__(self):
        return f"{self.name} - {self.price} บาท"


class OptionGroup(models.Model):
    restaurant = models.ForeignKey(
        Restaurant,
        on_delete=models.CASCADE,
        related_name='option_groups',
        verbose_name="ร้านอาหาร",
    )
    name = models.CharField(max_length=150, verbose_name="ชื่อกลุ่มตัวเลือก")

    def __str__(self):
        return f"{self.name} ({self.restaurant.name})"


class OptionItem(models.Model):
    group = models.ForeignKey(
        OptionGroup,
        on_delete=models.CASCADE,
        related_name='options',
        verbose_name="กลุ่มตัวเลือก",
    )
    name = models.CharField(max_length=150, verbose_name="ชื่อตัวเลือก")
    price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="ราคาบวกเพิ่ม (บาท)",
    )

    def __str__(self):
        return f"{self.name} (+{self.price} บาท)"


class Order(models.Model):
    ORDER_TYPE_CHOICES = (
        ('table', 'รับประทานที่ร้าน'),
        ('pickup', 'สั่งออนไลน์ รับที่ร้าน'),
        ('counter', 'หน้าร้าน รับกลับบ้าน'),
    )
    STATUS_CHOICES = (
        ('pending', 'รอดำเนินการ'),
        ('cooking', 'กำลังทำอาหาร'),
        ('completed', 'เสิร์ฟแล้ว/เสร็จสิ้น'),
        ('cancelled', 'ยกเลิก'),
    )
    PAYMENT_STATUS_CHOICES = (
        ('not_required', 'ไม่ต้องตรวจสอบ'),
        ('pending', 'รอตรวจสอบสลิป'),
        ('confirmed', 'ยืนยันการชำระเงินแล้ว'),
        ('rejected', 'ปฏิเสธสลิป'),
    )
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='orders')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_restaurant_orders',
        verbose_name="ผู้บันทึกออร์เดอร์",
    )
    table_number = models.CharField(max_length=50, verbose_name="เลขโต๊ะ / โซน")
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, default='table', verbose_name="ประเภทออร์เดอร์")
    customer_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="ชื่อผู้สั่ง")
    customer_phone = models.CharField(max_length=20, blank=True, verbose_name="เบอร์โทรผู้สั่ง")
    public_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    is_paid = models.BooleanField(default=False, verbose_name="ชำระเงินแล้วหรือยัง") # <--- เพิ่มฟิลด์ชำระเงิน
    pickup_at = models.DateTimeField(null=True, blank=True, verbose_name="เวลารับสินค้า")
    payment_slip = models.FileField(
        upload_to=payment_slip_upload_path,
        blank=True,
        validators=[validate_payment_slip],
        verbose_name="สลิปโอนเงิน",
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default='not_required',
        verbose_name="สถานะตรวจสอบการชำระเงิน",
    )
    is_collected = models.BooleanField(default=False, verbose_name="รับสินค้าแล้ว")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Order #{self.id} - โต๊ะ {self.table_number}"

class OrderItem(models.Model):
    STATUS_CHOICES = (
        ('pending', 'รอดำเนินการ'),
        ('cooking', 'กำลังทำอาหาร'),
        ('completed', 'เสิร์ฟแล้ว'),
        ('cancelled', 'ยกเลิก'),
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    selected_options = models.JSONField(default=list, blank=True, verbose_name="ตัวเลือกเสริมที่เลือก")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending') # <--- เพิ่มสถานะแยกแต่ละเมนู

    def __str__(self):
        return f"{self.menu_item.name} x {self.quantity} (โต๊ะ {self.order.table_number})"

class Table(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='tables', verbose_name="ร้านอาหาร")
    name = models.CharField(max_length=100, verbose_name="ชื่อโต๊ะ / โซน / จุดบริการ")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="วันที่สร้าง")

    def __str__(self):
        return f"{self.name} ({self.restaurant.name})"


class WaiterCall(models.Model):
    restaurant = models.ForeignKey(
        Restaurant,
        on_delete=models.CASCADE,
        related_name='waiter_calls',
        verbose_name="ร้านอาหาร",
    )
    table_number = models.CharField(max_length=50, verbose_name="เลขโต๊ะ")
    is_resolved = models.BooleanField(default=False, verbose_name="รับทราบแล้ว")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('created_at',)

    def __str__(self):
        return f"เรียกพนักงาน - โต๊ะ {self.table_number} ({self.restaurant.name})"


class RestaurantStaff(models.Model):
    ROLE_CHOICES = (
        ('order', 'พนักงานรับออร์เดอร์'),
        ('kitchen', 'พ่อครัว / หน้าจอครัว'),
    )
    restaurant = models.ForeignKey(
        Restaurant,
        on_delete=models.CASCADE,
        related_name='staff_members',
        verbose_name="ร้านอาหาร",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='restaurant_staff',
        verbose_name="บัญชีพนักงาน",
    )
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='order',
        verbose_name="หน้าที่",
    )
    is_active = models.BooleanField(default=True, verbose_name="เปิดใช้งาน")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('user__username',)
        verbose_name = "พนักงานร้าน"
        verbose_name_plural = "พนักงานร้าน"

    def __str__(self):
        return f"{self.user.get_username()} ({self.restaurant.name})"


class TransactionRecord(models.Model):
    """ โมเดลสำหรับบันทึกรายรับอื่นๆ หรือรายจ่ายของร้าน (เช่น ค่าวัตถุดิบ, ค่าน้ำ-ค่าไฟ) """
    TRANSACTION_TYPES = [
        ('income', 'รายรับอื่นๆ'),
        ('expense', 'รายจ่าย'),
    ]
    
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='transactions')
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES, default='expense')
    title = models.CharField(max_length=255)  # เช่น ค่าวัตถุดิบ, ค่าแก๊ส, ค่าน้ำ-ไฟ
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(default=timezone.localdate)
    note = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.get_transaction_type_display()}] {self.title} - ฿{self.amount}"

class ContactInquiry(models.Model):
    """ โมเดลสำหรับเก็บข้อมูลผู้สนใจสมัครใช้งานระบบจาก Landing Page """
    PACKAGE_CHOICES = [
        ('starter', 'Starter Plan (฿990 / เดือน)'),
        ('pro', 'Professional Pro (฿1,990 / เดือน)'),
    ]

    contact_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50)
    restaurant_name = models.CharField(max_length=255)
    package = models.CharField(max_length=50, choices=PACKAGE_CHOICES, default='starter')
    message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.contact_name} ({self.restaurant_name}) - {self.get_package_display()}"
