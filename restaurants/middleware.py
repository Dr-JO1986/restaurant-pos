from django.conf import settings
from django.http import HttpResponseForbidden
from django.urls import Resolver404, resolve

from .models import RestaurantStaff


class StaffAccessMiddleware:
    allowed_staff_views = {
        'staff_order_screen',
        'merchant_login',
        'merchant_logout',
        'menu_item_image',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
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
                if view_name not in self.allowed_staff_views and not is_own_restaurant_bill:
                    return HttpResponseForbidden()

        return self.get_response(request)
