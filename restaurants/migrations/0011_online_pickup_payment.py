import uuid

import restaurants.models
from django.db import migrations, models


def assign_public_tokens(apps, schema_editor):
    Order = apps.get_model('restaurants', 'Order')
    database = schema_editor.connection.alias
    for order in Order.objects.using(database).filter(public_token__isnull=True).iterator():
        order.public_token = uuid.uuid4()
        order.save(update_fields=['public_token'])


class Migration(migrations.Migration):

    dependencies = [
        ('restaurants', '0010_contactinquiry'),
    ]

    operations = [
        migrations.AddField(
            model_name='restaurant',
            name='bank_name',
            field=models.CharField(blank=True, max_length=100, verbose_name='ธนาคารรับโอน'),
        ),
        migrations.AddField(
            model_name='restaurant',
            name='bank_account_name',
            field=models.CharField(blank=True, max_length=100, verbose_name='ชื่อบัญชี'),
        ),
        migrations.AddField(
            model_name='restaurant',
            name='bank_account_number',
            field=models.CharField(blank=True, max_length=50, verbose_name='เลขบัญชี'),
        ),
        migrations.AddField(
            model_name='order',
            name='order_type',
            field=models.CharField(
                choices=[('table', 'รับประทานที่ร้าน'), ('pickup', 'สั่งออนไลน์ รับที่ร้าน')],
                default='table',
                max_length=20,
                verbose_name='ประเภทออร์เดอร์',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='customer_phone',
            field=models.CharField(blank=True, max_length=20, verbose_name='เบอร์โทรผู้สั่ง'),
        ),
        migrations.AddField(
            model_name='order',
            name='public_token',
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='pickup_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='เวลารับสินค้า'),
        ),
        migrations.AddField(
            model_name='order',
            name='payment_slip',
            field=models.FileField(
                blank=True,
                upload_to=restaurants.models.payment_slip_upload_path,
                validators=[restaurants.models.validate_payment_slip],
                verbose_name='สลิปโอนเงิน',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='payment_status',
            field=models.CharField(
                choices=[
                    ('not_required', 'ไม่ต้องตรวจสอบ'),
                    ('pending', 'รอตรวจสอบสลิป'),
                    ('confirmed', 'ยืนยันการชำระเงินแล้ว'),
                    ('rejected', 'ปฏิเสธสลิป'),
                ],
                default='not_required',
                max_length=20,
                verbose_name='สถานะตรวจสอบการชำระเงิน',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='is_collected',
            field=models.BooleanField(default=False, verbose_name='รับสินค้าแล้ว'),
        ),
        migrations.RunPython(assign_public_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order',
            name='public_token',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
