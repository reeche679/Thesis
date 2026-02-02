"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView
import sys
from pathlib import Path

# Add project root to path to import views
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from views import (
    pos_main, pos_home, pos_generate, pos_preview,
    pos_history, pos_about, login_view, register_view, sign_out,
    contact_us, help_page, save_pos_grades, set_academic_term, delete_pos_record
)

urlpatterns = [
    path('admin/', admin.site.urls),
    # Main Window (landing page)
    path('', pos_main, name='pos_main'),
    # POS pages
    path('home/', pos_home, name='pos_home'),
    path('home/set-term/', set_academic_term, name='set_academic_term'),
    path('generate/', pos_generate, name='pos_generate'),
    path('generate/save-grades/', save_pos_grades, name='save_pos_grades'),
    path('preview/', pos_preview, name='pos_preview'),
    path('history/<int:record_id>/delete/', delete_pos_record, name='delete_pos_record'),
    path('history/', pos_history, name='pos_history'),
    path('about/', pos_about, name='pos_about'),
    # Auth pages
    path('login/', login_view, name='login'),
    path('register/', register_view, name='register'),
    path('signout/', sign_out, name='sign_out'),
    # Support pages
    path('contact/', contact_us, name='contact_us'),
    path('help/', help_page, name='help'),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
