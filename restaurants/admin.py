from django.contrib import admin
from .models import Restaurant, Category, MenuItem, OptionGroup, OptionItem
from .models import TransactionRecord
from .models import ContactInquiry

@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'owner_name', 'phone', 'is_active', 'created_at')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(TransactionRecord)
class TransactionRecordAdmin(admin.ModelAdmin):
    list_display = ('restaurant', 'transaction_type', 'title', 'amount', 'date')
    list_filter = ('transaction_type', 'date', 'restaurant')
    search_fields = ('title', 'restaurant__name')

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'restaurant')
    list_filter = ('restaurant',)

@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'restaurant', 'category', 'price', 'is_available')
    list_filter = ('restaurant', 'category', 'is_available')
    filter_horizontal = ('option_groups',)


class OptionItemInline(admin.TabularInline):
    model = OptionItem
    extra = 1


@admin.register(OptionGroup)
class OptionGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'restaurant')
    list_filter = ('restaurant',)
    inlines = [OptionItemInline]


@admin.register(OptionItem)
class OptionItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'group', 'price')
    list_filter = ('group__restaurant', 'group')



@admin.register(ContactInquiry)
class ContactInquiryAdmin(admin.ModelAdmin):
    list_display = ('contact_name', 'phone', 'restaurant_name', 'package', 'created_at')
    list_filter = ('package', 'created_at')
    search_fields = ('contact_name', 'restaurant_name', 'phone')
