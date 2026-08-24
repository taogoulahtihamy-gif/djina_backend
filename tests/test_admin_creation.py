from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import AdminProfile


User = get_user_model()


class AdminCreationTests(APITestCase):
    url = "/api/admin/users/create-admin/"

    def setUp(self):
        self.super_admin = User.objects.create_superuser(
            email="root@djina.test", phone="+23560000001", password="StrongRootPass!42",
            user_type=User.UserType.ADMIN,
        )
        AdminProfile.objects.create(user=self.super_admin, type_of=AdminProfile.AdminType.SUPER)
        self.payload = {
            "first_name": "Amina",
            "last_name": "Mahamat",
            "email": "amina@djina.test",
            "phone": "+23560000002",
            "password": "SecureAdminPass!42",
            "admin_type": "simple",
        }

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def test_super_admin_creates_simple_admin(self):
        self.authenticate(self.super_admin)
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email=self.payload["email"])
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password(self.payload["password"]))
        self.assertEqual(user.admin_profile.type_of, AdminProfile.AdminType.SIMPLE)
        self.assertNotIn("password", response.data)

    def test_super_admin_creates_super_admin(self):
        self.authenticate(self.super_admin)
        payload = {**self.payload, "admin_type": "super"}
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email=payload["email"])
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.admin_profile.type_of, AdminProfile.AdminType.SUPER)

    def test_simple_admin_is_forbidden(self):
        admin = User.objects.create_user(
            email="admin@djina.test", phone="+23560000003", password="StrongAdminPass!42",
            user_type=User.UserType.ADMIN, is_staff=True,
        )
        AdminProfile.objects.create(user=admin, type_of=AdminProfile.AdminType.SIMPLE)
        self.authenticate(admin)
        self.assertEqual(self.client.post(self.url, self.payload, format="json").status_code, status.HTTP_403_FORBIDDEN)

    def test_customer_is_forbidden(self):
        customer = User.objects.create_user(
            email="customer@djina.test", phone="+23560000004", password="StrongCustomerPass!42",
        )
        self.authenticate(customer)
        self.assertEqual(self.client.post(self.url, self.payload, format="json").status_code, status.HTTP_403_FORBIDDEN)

    def test_driver_is_forbidden(self):
        driver = User.objects.create_user(
            email="driver@djina.test", phone="+23560000005", password="StrongDriverPass!42",
            user_type=User.UserType.DRIVER,
        )
        self.authenticate(driver)
        self.assertEqual(self.client.post(self.url, self.payload, format="json").status_code, status.HTTP_403_FORBIDDEN)

    def test_duplicate_email_is_rejected(self):
        self.authenticate(self.super_admin)
        payload = {**self.payload, "email": self.super_admin.email}
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_duplicate_phone_is_rejected(self):
        self.authenticate(self.super_admin)
        payload = {**self.payload, "phone": self.super_admin.phone}
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("phone", response.data)

    def test_weak_password_is_rejected(self):
        self.authenticate(self.super_admin)
        payload = {**self.payload, "password": "12345678"}
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("non_field_errors", response.data)
