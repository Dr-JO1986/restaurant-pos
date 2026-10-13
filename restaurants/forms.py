from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.utils.text import slugify
from .models import MenuItem, Category, RESTAURANT_TYPES, Restaurant

class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500', 'placeholder': 'ชื่อหมวดหมู่ เช่น เครื่องดื่ม, ของหวาน'}),
        }

class MenuItemForm(forms.ModelForm):
    class Meta:
        model = MenuItem
        fields = ['category', 'name', 'price', 'description', 'image', 'is_available']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500', 'placeholder': 'ชื่อเมนู เช่น ข้าวผัดกุ้ง'}),
            'category': forms.Select(attrs={'class': 'w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500'}),
            'price': forms.NumberInput(attrs={'class': 'w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500', 'placeholder': '0.00'}),
            'description': forms.Textarea(attrs={'class': 'w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500', 'rows': 3, 'placeholder': 'รายละเอียดส่วนผสมหรือรสชาติ'}),
            'image': forms.ClearableFileInput(attrs={'class': 'w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100'}),
            'is_available': forms.CheckboxInput(attrs={'class': 'w-5 h-5 text-indigo-600 rounded focus:ring-indigo-500'}),
        }


class StaffAccountCreationForm(UserCreationForm):
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={
            'class': 'w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500',
            'autocomplete': 'email',
        }),
    )

    class Meta:
        model = get_user_model()
        fields = ('username', 'email')
        widgets = {
            'username': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500',
                'autocomplete': 'username',
            }),
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = False
        user.is_superuser = False
        user.is_active = True
        if commit:
            user.save()
        return user


class MerchantRegistrationForm(UserCreationForm):
    restaurant_name = forms.CharField(
        max_length=255,
        label='ชื่อร้าน',
        widget=forms.TextInput(attrs={
            'class': 'w-full rounded-lg border px-4 py-2',
            'autocomplete': 'organization',
        }),
    )
    owner_name = forms.CharField(
        max_length=255,
        label='ชื่อเจ้าของร้าน',
        widget=forms.TextInput(attrs={
            'class': 'w-full rounded-lg border px-4 py-2',
            'autocomplete': 'name',
        }),
    )
    phone = forms.CharField(
        max_length=20,
        label='เบอร์โทรศัพท์',
        widget=forms.TelInput(attrs={
            'class': 'w-full rounded-lg border px-4 py-2',
            'autocomplete': 'tel',
        }),
    )
    restaurant_type = forms.ChoiceField(
        choices=RESTAURANT_TYPES,
        label='ประเภทร้าน',
        widget=forms.Select(attrs={'class': 'w-full rounded-lg border px-4 py-2'}),
    )

    class Meta:
        model = get_user_model()
        fields = ('username', 'restaurant_name', 'owner_name', 'phone', 'restaurant_type')
        widgets = {
            'username': forms.TextInput(attrs={
                'class': 'w-full rounded-lg border px-4 py-2',
                'autocomplete': 'username',
            }),
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = False
        user.is_superuser = False
        user.is_active = True
        if commit:
            user.save()

            base_slug = slugify(self.cleaned_data['restaurant_name']) or 'restaurant'
            slug = base_slug
            suffix = 2
            while Restaurant.objects.filter(slug=slug).exists():
                slug = f'{base_slug}-{suffix}'
                suffix += 1

            Restaurant.objects.create(
                user=user,
                name=self.cleaned_data['restaurant_name'],
                slug=slug,
                owner_name=self.cleaned_data['owner_name'],
                phone=self.cleaned_data['phone'],
                restaurant_type=self.cleaned_data['restaurant_type'],
                is_active=False,
            )
        return user