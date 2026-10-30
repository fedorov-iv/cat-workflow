from django.urls import include, path
from drf_spectacular.views import SpectacularSwaggerView

from . import views

arm_patterns = ([
    path('', views.application_list, name='application-list'),
    path('filters/<str:kind>/', views.filter_options, name='filter-options'),
    path('applications/<int:pk>/', views.application_detail, name='application-detail'),
    path('applications/<int:pk>/transitions/<int:transition_id>/', views.application_transition,
         name='application-transition'),
], 'arm')

api_patterns = ([
    path('docs/', SpectacularSwaggerView.as_view(url_name='api:schema'), name='docs'),
    path('schema/', views.api_schema, name='schema'),
    path('applications/<int:pk>/transitions/', views.api_transitions, name='transitions'),
    path('applications/<int:pk>/status/', views.api_change_status, name='change-status'),
], 'api')

urlpatterns = [
    path('arm/', include(arm_patterns)),
    path('api/', include(api_patterns)),
]
