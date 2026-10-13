from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from .models import Restaurant, RestaurantStaff

def merchant_login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            staff_membership = RestaurantStaff.objects.filter(user=user).select_related('restaurant').first()
            if staff_membership:
                if not staff_membership.is_active:
                    return render(request, 'restaurants/login.html', {
                        'error': 'บัญชีพนักงานนี้ถูกปิดใช้งาน กรุณาติดต่อเจ้าของร้าน',
                    })
                login(request, user)
                if not staff_membership.restaurant.is_active:
                    return redirect('restaurant_pending')
                return redirect('staff_order_screen', slug=staff_membership.restaurant.slug)

            login(request, user)
            if user.is_superuser:
                return redirect('restaurant_approval_list')
            # ค้นหาร้านค้าที่ผูกกับ User นี้
            try:
                restaurant = Restaurant.objects.get(user=user)
                if not restaurant.is_active:
                    return redirect('restaurant_pending')
                return redirect('merchant_dashboard', slug=restaurant.slug)
            except Restaurant.DoesNotExist:
                if user.is_superuser:
                    return redirect('restaurant_approval_list')
                return redirect('merchant_register')
        else:
            return render(request, 'restaurants/login.html', {'error': 'Username หรือ Password ไม่ถูกต้อง'})
    return render(request, 'restaurants/login.html')

def merchant_logout(request):
    logout(request)
    return redirect('merchant_login')