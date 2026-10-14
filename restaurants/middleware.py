from django.conf import settings
from django.http import Http404, HttpResponseForbidden
from django.urls import Resolver404, resolve

from .models import Restaurant, RestaurantStaff


class RestaurantApprovalMiddleware:
    allowed_views = {
        'landing_page',
        'merchant_login',
        'merchant_logout',
        'merchant_register',
        'restaurant_pending',
        'restaurant_approval_list',
        'toggle_restaurant_active',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            match = resolve(request.path_info)
        except Resolver404:
            return self.get_response(request)

        if match.url_name in self.allowed_views or 'slug' not in match.kwargs:
            return self.get_response(request)

        restaurant = Restaurant.objects.filter(slug=match.kwargs['slug']).first()
        if restaurant is None or restaurant.is_active or request.user.is_superuser:
            return self.get_response(request)

        if request.user.is_authenticated:
            is_owner = restaurant.user_id == request.user.id
            is_staff_member = RestaurantStaff.objects.filter(
                restaurant=restaurant,
                user=request.user,
            ).exists()
            if is_owner or is_staff_member:
                from django.shortcuts import redirect

                return redirect('restaurant_pending')

        raise Http404


class StaffAccessMiddleware:
    allowed_staff_views = {
        'staff_order_screen',
        'merchant_login',
        'merchant_logout',
        'menu_item_image',
        'restaurant_pending',
        'kitchen_display',
        'kitchen_live_updates',
        'kitchen_update_order_status',
        'kitchen_update_item_status',
        'call_waiter',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            if user.is_superuser:
                return self.get_response(request)

            try:
                staff_membership = user.restaurant_staff
            except RestaurantStaff.DoesNotExist:
                staff_membership = None

            if staff_membership:
                static_prefix = f"/{settings.STATIC_URL.strip('/')}/"
                if request.path_info.startswith(static_prefix):
                    return self.get_response(request)

                try:
                    match = resolve(request.path_info)
                    view_name = match.url_name
                except Resolver404:
                    match = None
                    view_name = None

                is_own_restaurant_bill = (
                    staff_membership.is_active
                    and view_name == 'table_bill_summary'
                    and match.kwargs.get('slug') == staff_membership.restaurant.slug
                )
                is_own_restaurant_request = (
                    match is not None
                    and match.kwargs.get('slug') == staff_membership.restaurant.slug
                )
                kitchen_views = {
                    'kitchen_display',
                    'kitchen_live_updates',
                    'kitchen_update_order_status',
                    'kitchen_update_item_status',
                }
                if staff_membership.role == 'kitchen':
                    allowed = (
                        view_name in kitchen_views
                        and is_own_restaurant_request
                    ) or view_name in {
                        'merchant_login',
                        'merchant_logout',
                        'restaurant_pending',
                    }
                else:
                    allowed = (
                        view_name not in kitchen_views
                        and (view_name in self.allowed_staff_views or is_own_restaurant_bill)
                    )
                if not staff_membership.is_active or not allowed:
                    return HttpResponseForbidden()

        return self.get_response(request)
