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
    path('delete_all/',      views.delete_all,      name='delete_all'),
    path('delete_selected/', views.delete_selected, name='delete_selected'),
    path('run-crawler/', views.run_crawler, name='run_crawler'),
    path('get-crawler-state/', views.get_crawler_state, name='get_crawler_state'),
    path('stop-crawler/', views.stop_crawler, name='stop_crawler'),
    path('get-logs/', views.get_logs, name='get_logs'),
]