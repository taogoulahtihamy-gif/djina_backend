from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import Commission, Course
from core.services.commission_service import ensure_commission_for_course


class Command(BaseCommand):
    help = "Create missing commissions for completed historical courses after explicit review."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true", help="Preview without writing to the database.")
        mode.add_argument("--apply", action="store_true", help="Create missing commissions.")
        parser.add_argument("--from-date", help="Only include courses completed on or after YYYY-MM-DD.")

    def handle(self, *args, **options):
        queryset = Course.objects.filter(status=Course.Status.COMPLETED, deleted_at__isnull=True).order_by("pk")
        if options.get("from_date"):
            try:
                from_date = datetime.strptime(options["from_date"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError("--from-date must use YYYY-MM-DD.") from exc
            queryset = queryset.filter(completed_at__date__gte=from_date)

        existing_course_ids = set(Commission.objects.values_list("course_id", flat=True))
        candidates = []
        ignored_without_driver = 0
        ignored_existing = 0
        for course in queryset.iterator():
            if course.driver_id is None:
                ignored_without_driver += 1
            elif course.pk in existing_course_ids:
                ignored_existing += 1
            else:
                candidates.append(course)

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN: {len(candidates)} commission(s) would be created; "
                    f"{ignored_existing} existing and {ignored_without_driver} without driver ignored."
                )
            )
            return

        created = 0
        with transaction.atomic():
            for course in candidates:
                before = Commission.objects.filter(course_id=course.pk).exists()
                ensure_commission_for_course(course)
                if not before and Commission.objects.filter(course_id=course.pk).exists():
                    created += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"{created} commission(s) created; {ignored_existing} existing and "
                f"{ignored_without_driver} without driver ignored at {timezone.now().isoformat()}."
            )
        )
