#!/usr/init/env bash
# ออกจากโปรแกรมทันทีหากมีข้อผิดพลาด
set -o errexit

# ติดตั้งไลบรารีทั้งหมด
pip install -r requirements.txt

# รวบรวม Static files (เช่น CSS, รูปภาพ ของ Tailwind)
python manage.py collectstatic --no-input

# อัปเดตโครงสร้างฐานข้อมูล
python manage.py migrate