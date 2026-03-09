from django.urls import path

from .views import (
    pos_main,
    pos_home,
    pos_generate,
    pos_preview,
    download_pos_pdf,
    download_pos_pdf_record,
    pos_history,
    pos_about,
    login_view,
    register_view,
    sign_out,
    contact_us,
    help_page,
    save_pos_grades,
    set_academic_term,
    delete_pos_record,
    validate_pos,
    upload_pos_grades,
    generate_pos_api,
)

urlpatterns = [
    # Login as landing page
    path("", login_view, name="login"),
    path("login/", login_view),
    path("main/", pos_main, name="pos_main"),

    path("home/", pos_home, name="pos_home"),
    path("home/set-term/", set_academic_term, name="set_academic_term"),

    path("generate/", pos_generate, name="pos_generate"),
    path("generate/save-grades/", save_pos_grades, name="save_pos_grades"),
    path("generate/validate-pos/", validate_pos, name="validate_pos"),
    path("generate/upload-grades/", upload_pos_grades, name="upload_pos_grades"),
    path("generate/api/", generate_pos_api, name="generate_pos_api"),

    path("preview/", pos_preview, name="pos_preview"),
    path("preview/download-pdf/", download_pos_pdf, name="download_pos_pdf"),

    path("history/<int:record_id>/delete/", delete_pos_record, name="delete_pos_record"),
    path(
        "history/<int:record_id>/download-pdf/",
        download_pos_pdf_record,
        name="download_pos_pdf_record",
    ),
    path("history/", pos_history, name="pos_history"),
    path("about/", pos_about, name="pos_about"),

    path("register/", register_view, name="register"),
    path("signout/", sign_out, name="sign_out"),
    path("contact/", contact_us, name="contact_us"),
    path("help/", help_page, name="help"),
]
