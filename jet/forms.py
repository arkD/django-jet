import json

from django import forms
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import Q
import operator

from jet.models import Bookmark, PinnedApplication
from jet.utils import get_model_instance_label, user_is_authenticated
from functools import reduce

try:
    from django.apps import apps
    get_model = apps.get_model
except ImportError:
    from django.db.models.loading import get_model


class AddBookmarkForm(forms.ModelForm):
    def __init__(self, request, *args, **kwargs):
        self.request = request
        super(AddBookmarkForm, self).__init__(*args, **kwargs)

    class Meta:
        model = Bookmark
        fields = ['url', 'title']

    def clean(self):
        data = super(AddBookmarkForm, self).clean()
        if not user_is_authenticated(self.request.user) or not self.request.user.is_staff:
            raise ValidationError('error')
        if not self.request.user.has_perm('jet.change_bookmark'):
            raise ValidationError('error')
        return data

    def save(self, commit=True):
        self.instance.user = self.request.user.pk
        return super(AddBookmarkForm, self).save(commit)


class RemoveBookmarkForm(forms.ModelForm):
    def __init__(self, request, *args, **kwargs):
        self.request = request
        super(RemoveBookmarkForm, self).__init__(*args, **kwargs)

    class Meta:
        model = Bookmark
        fields = []

    def clean(self):
        data = super(RemoveBookmarkForm, self).clean()
        if not user_is_authenticated(self.request.user) or not self.request.user.is_staff:
            raise ValidationError('error')
        if self.instance.user != self.request.user.pk:
            raise ValidationError('error')
        return data

    def save(self, commit=True):
        if commit:
            self.instance.delete()


class ToggleApplicationPinForm(forms.ModelForm):
    def __init__(self, request, *args, **kwargs):
        self.request = request
        super(ToggleApplicationPinForm, self).__init__(*args, **kwargs)

    class Meta:
        model = PinnedApplication
        fields = ['app_label']

    def clean(self):
        data = super(ToggleApplicationPinForm, self).clean()
        if not user_is_authenticated(self.request.user) or not self.request.user.is_staff:
            raise ValidationError('error')
        return data

    def save(self, commit=True):
        if commit:
            try:
                pinned_app = PinnedApplication.objects.get(
                    app_label=self.cleaned_data['app_label'],
                    user=self.request.user.pk
                )
                pinned_app.delete()
                return False
            except PinnedApplication.DoesNotExist:
                PinnedApplication.objects.create(
                    app_label=self.cleaned_data['app_label'],
                    user=self.request.user.pk
                )
                return True


class ModelLookupForm(forms.Form):
    app_label = forms.CharField()
    model = forms.CharField()
    field_name = forms.CharField(required=False)
    field_model = forms.CharField(required=False)
    q = forms.CharField(required=False)
    page = forms.IntegerField(required=False)
    page_size = forms.IntegerField(required=False, min_value=1, max_value=1000)
    # object_id = forms.IntegerField(required=False)
    model_cls = None

    def __init__(self, request, *args, **kwargs):
        self.request = request
        super(ModelLookupForm, self).__init__(*args, **kwargs)

    def clean(self):
        data = super(ModelLookupForm, self).clean()

        if not user_is_authenticated(self.request.user) or not self.request.user.is_staff:
            raise ValidationError('error')

        try:
            self.model_cls = get_model(data['app_label'], data['model'])
        except:
            raise ValidationError('error')

        content_type = ContentType.objects.get_for_model(self.model_cls)
        permission = Permission.objects.filter(content_type=content_type, codename__startswith='change_').first()

        if not self.request.user.has_perm('{}.{}'.format(data['app_label'], permission.codename)):
            raise ValidationError('error')

        return data

    def lookup(self):
        qs = self.model_cls.objects.all()

        if getattr(self.model_cls, 'autocomplete_select_related_fields', None):
            qs = qs.select_related(*self.model_cls.autocomplete_select_related_fields())

        if getattr(self.model_cls, 'autocomplete_prefetch_related_fields', None):
            qs = qs.prefetch_related(*self.model_cls.autocomplete_prefetch_related_fields())

        if getattr(self.model_cls, 'autocomplete_queryset_filters', None):
            source_object_id = 0  # false, not a model object
            try:
                referer_parts = self.request.META.get('HTTP_REFERER', '').split('/')
                model_name = self.cleaned_data['field_model'].split('.')[1].lower()
                field_model_index = [idx for idx, s in enumerate(referer_parts) if s == model_name][0]
                source_id = referer_parts[field_model_index + 1]
                if source_id.isdecimal():
                    source_object_id = int(source_id)
            except IndexError:
                pass

            filters = self.model_cls.autocomplete_queryset_filters(self.cleaned_data['field_model'],
                                                                   self.cleaned_data['field_name'],
                                                                   source_object_id)
            qs = qs.filter(**filters)

        if self.cleaned_data['q']:
            if getattr(self.model_cls, 'autocomplete_search_fields', None):
                search_fields = self.model_cls.autocomplete_search_fields()
                filter_data = [
                    Q(**{f"{field}__icontains": self.cleaned_data['q']})
                    for field in search_fields
                ]
                filter_query = reduce(operator.or_, filter_data)
                qs = qs.filter(filter_query).distinct()
            else:
                qs = qs.none()

        limit = self.cleaned_data['page_size'] or 100
        page = self.cleaned_data['page'] or 1
        offset = (page - 1) * limit

        items = [
            {
                'id': instance.pk,
                'text': get_model_instance_label(instance)
            }
            for instance in qs.distinct()[offset:offset + limit]
        ]
        
        total = qs.count()

        # gives a possibility to select an empty value
        items.insert(0, {'id': 0, 'text': '----------'})

        return items, total
