"""Technical report to attach to a support request.

Two things this page is careful about, and both are promises the product has
already made elsewhere.

IT SENDS NOTHING. There is no upload button, because the application promises
that the data stays on the machine. The report is shown, saved and copied;
attaching it to a message is the user's own act.

IT SHOWS WHAT IT WILL HAND OVER. The document is on screen before it is saved,
in full. Asking someone to send a file they have not read is the opposite of
what "no data leaves your computer" means to them.

The content, and the guarantee that it carries nothing personal, live in
services/diagnostics_service.py.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import sessionmaker

from services.diagnostics_service import collect, to_xml
from services.settings_service import SettingsService
from ui.i18n import t


def _yes_no(value: bool) -> str:
    return t("diagnostics.yes") if value else t("diagnostics.no")


def render_diagnostics_page(engine) -> None:
    st.title(t("diagnostics.title"))
    st.caption(t("diagnostics.intro"))

    svc = SettingsService(engine)
    settings = svc.get_all()

    session = sessionmaker(bind=engine)()
    try:
        report = collect(session, settings)
    finally:
        session.close()

    app = report["application"]
    system = report["system"]
    graphics = report["graphics"]
    inference = report["inference"]

    # The three answers somebody asking for help needs first: which build is
    # running, whether the graphics card is doing anything, and how the model
    # is configured. Everything else is detail below.
    c1, c2, c3 = st.columns(3)
    c1.metric(t("diagnostics.metric.version"), app["version"])
    c2.metric(
        t("diagnostics.metric.acceleration"),
        _yes_no(graphics["acceleration_active"]),
        help=t("diagnostics.metric.acceleration_help"),
    )
    c3.metric(t("diagnostics.metric.context"), inference["n_ctx"])

    if not graphics["acceleration_active"] and graphics["gpu"] not in ("", "unknown"):
        # Having a card and using it are different facts. Saying so plainly
        # here spares the next person the afternoon it cost us.
        st.info(t("diagnostics.gpu_present_not_used").format(gpu=graphics["gpu"]))

    st.subheader(t("diagnostics.section.system"))
    st.write({
        t("diagnostics.field.os"): system["os_version"],
        t("diagnostics.field.arch"): system["arch"],
        t("diagnostics.field.ram"): f"{system['ram_gb']} GB",
        t("diagnostics.field.gpu"): graphics["gpu"],
        t("diagnostics.field.devices"): ", ".join(graphics["inference_devices"]) or "-",
        t("diagnostics.field.install"): app["install_method"],
        t("diagnostics.field.build"): app["build_time"],
    })

    st.subheader(t("diagnostics.section.inference"))
    st.write({
        t("diagnostics.field.backend"): inference["backend"],
        t("diagnostics.field.model"): inference["model"] or "-",
        t("diagnostics.field.context"): inference["n_ctx"],
        t("diagnostics.field.gpu_layers"): inference["n_gpu_layers"],
        t("diagnostics.field.library"): inference["llama_cpp_version"],
    })

    st.subheader(t("diagnostics.section.imports"))
    imports = report["imports"]
    if imports["rows"]:
        st.write({
            t("diagnostics.field.files"): imports["files"],
            t("diagnostics.field.rows"): imports["rows"],
        })
        # Seconds per row, not total duration: it is the only form that can be
        # compared between two machines.
        st.caption(t("diagnostics.per_row"))
        st.table({
            t("diagnostics.field.phase"): list(imports["per_row_seconds"].keys()),
            t("diagnostics.field.seconds_per_row"): list(imports["per_row_seconds"].values()),
        })
    else:
        st.caption(t("diagnostics.no_imports"))

    st.subheader(t("diagnostics.section.rating"))
    stars = st.select_slider(
        t("diagnostics.rating.label"),
        options=[0, 1, 2, 3, 4, 5],
        value=0,
        format_func=lambda n: "⭐" * n if n else t("diagnostics.rating.none"),
    )

    st.subheader(t("diagnostics.section.document"))
    xml = to_xml(report, stars=stars or None)
    st.caption(t("diagnostics.document.caption"))
    st.code(xml, language="xml")
    st.download_button(
        t("diagnostics.download"),
        data=xml,
        file_name=f"spendifai-report-{app['version']}.xml",
        mime="application/xml",
    )
