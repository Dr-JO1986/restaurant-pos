from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from .models import Restaurant

def merchant_login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            # ค้นหาร้านค้าที่ผูกกับ User นี้
            try:
                restaurant = Restaurant.objects.get(user=user)
                return redirect('merchant_dashboard', slug=restaurant.slug)
            except Restaurant.DoesNotExist:
                return redirect('/admin/') # ถ้ายังไม่มีร้าน ให้ไปหน้าแอดมินหรือหน้าตั้งค่า
        else:
            return render(request, 'restaurants/login.html', {'error': 'Username หรือ Password ไม่ถูกต้อง'})
    return render(request, 'restaurants/login.html')

def merchant_logout(request):
    logout(request)
    return redirect('merchant_login')