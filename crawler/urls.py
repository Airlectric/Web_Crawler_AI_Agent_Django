from django.contrib import admin
from django.urls import path
from crawler import views

urlpatterns = [
    path('', views.index, name='index'),
    path('parameters/', views.parameters, name='parameters'),
    path('files/', views.files, name='files'),
    path('database/', views.database, name='database'),
    path('edit/<int:id>/', views.edit_row, name='edit_row'),
    path('delete/<int:id>/', views.delete_row, name='delete_row'),
    path('run-crawler/', views.run_crawler, name='run_crawler'),
    path('stop-crawler/', views.stop_crawler, name='stop_crawler'),
    path('get-logs/', views.get_logs, name='get_logs'),
]