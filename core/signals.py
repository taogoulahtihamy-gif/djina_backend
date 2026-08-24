from asgiref.sync import (
    async_to_sync,
)

from channels.layers import (
    get_channel_layer,
)

from django.db import (
    transaction,
)

from django.db.models.signals import (
    post_delete,
    post_save,
)

from django.dispatch import (
    receiver,
)

from core.consumers import (
    ADMIN_REALTIME_GROUP,
)

from core.models import (
    Complaint,
    Course,
    DriverDocument,
    Payment,
)


def send_admin_event(
    resource,
    action,
    object_id,
):
    channel_layer = (
        get_channel_layer()
    )

    if channel_layer is None:
        return

    async_to_sync(
        channel_layer.group_send,
    )(
        ADMIN_REALTIME_GROUP,
        {
            'type':
                'admin.event',

            'resource':
                resource,

            'action':
                action,

            'object_id':
                object_id,
        },
    )


def schedule_admin_event(
    resource,
    action,
    object_id,
):
    transaction.on_commit(
        lambda: send_admin_event(
            resource,
            action,
            object_id,
        ),
    )


# =========================================================
# COURSES
# =========================================================

@receiver(
    post_save,
    sender=Course,
)
def course_saved(
    sender,
    instance,
    created,
    **kwargs,
):
    schedule_admin_event(
        resource='courses',

        action=(
            'created'
            if created
            else 'updated'
        ),

        object_id=
            instance.pk,
    )


@receiver(
    post_delete,
    sender=Course,
)
def course_deleted(
    sender,
    instance,
    **kwargs,
):
    schedule_admin_event(
        resource='courses',

        action='deleted',

        object_id=
            instance.pk,
    )


# =========================================================
# DOCUMENTS
# =========================================================

@receiver(
    post_save,
    sender=DriverDocument,
)
def driver_document_saved(
    sender,
    instance,
    created,
    **kwargs,
):
    schedule_admin_event(
        resource='documents',

        action=(
            'created'
            if created
            else 'updated'
        ),

        object_id=
            instance.pk,
    )


@receiver(
    post_delete,
    sender=DriverDocument,
)
def driver_document_deleted(
    sender,
    instance,
    **kwargs,
):
    schedule_admin_event(
        resource='documents',

        action='deleted',

        object_id=
            instance.pk,
    )


# =========================================================
# PAIEMENTS
# =========================================================

@receiver(
    post_save,
    sender=Payment,
)
def payment_saved(
    sender,
    instance,
    created,
    **kwargs,
):
    schedule_admin_event(
        resource='payments',

        action=(
            'created'
            if created
            else 'updated'
        ),

        object_id=
            instance.pk,
    )


@receiver(
    post_delete,
    sender=Payment,
)
def payment_deleted(
    sender,
    instance,
    **kwargs,
):
    schedule_admin_event(
        resource='payments',

        action='deleted',

        object_id=
            instance.pk,
    )


# =========================================================
# RÉCLAMATIONS
# =========================================================

@receiver(
    post_save,
    sender=Complaint,
)
def complaint_saved(
    sender,
    instance,
    created,
    **kwargs,
):
    schedule_admin_event(
        resource='complaints',

        action=(
            'created'
            if created
            else 'updated'
        ),

        object_id=
            instance.pk,
    )


@receiver(
    post_delete,
    sender=Complaint,
)
def complaint_deleted(
    sender,
    instance,
    **kwargs,
):
    schedule_admin_event(
        resource='complaints',

        action='deleted',

        object_id=
            instance.pk,
    )