"""
Test for the NotificationFilter class.
"""
from datetime import timedelta
from unittest import mock

import ddt
from django.utils.timezone import now

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory, CourseEnrollmentFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.django_comment_common.models import (
    Role,
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_STUDENT,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    )
from openedx.core.djangoapps.notifications.audience_filters import (
    EnrollmentAudienceFilter,
    RoleAudienceFilter,
)
from openedx.core.djangoapps.notifications.filters import NotificationFilter
from openedx.core.djangoapps.notifications.handlers import calculate_course_wide_notification_audience
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from openedx.features.course_experience.tests.views.helpers import add_course_mode
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


FORUM_ROLES = [
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_STUDENT,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
]

@ddt.ddt
class CourseExpirationTestCase(ModuleStoreTestCase):
    """Tests to verify the get_user_course_expiration_date function is working correctly"""

    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        self.course = CourseFactory(
            start=now() - timedelta(weeks=10),
        )

        self.user = UserFactory()
        self.user_1 = UserFactory()

        # Make this a verified course, so we can test expiration date
        add_course_mode(self.course, mode_slug=CourseMode.AUDIT)
        add_course_mode(self.course)
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        expired_audit = CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        expired_audit.created = now() - timedelta(weeks=6)
        expired_audit.save()

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    def test_audit_expired_filter(
        self,
        mock_get_course_run_details,
    ):
        """
        Test if filter_audit_expired function is working correctly
        """

        mock_get_course_run_details.return_value = {'weeks_to_complete': 4}
        result = NotificationFilter.filter_audit_expired(
            [self.user.id, self.user_1.id],
            self.course,
        )
        self.assertEqual([self.user_1.id], result)

        mock_get_course_run_details.return_value = {'weeks_to_complete': 7}
        result = NotificationFilter.filter_audit_expired(
            [self.user.id, self.user_1.id],
            self.course,
        )
        self.assertEqual([self.user.id, self.user_1.id], result)

        CourseDurationLimitConfig.objects.create(
            enabled=True,
            course=CourseOverview.get_from_id(self.course.id),
            enabled_as_of=now(),
        )
        # weeks_to_complete is set to 4 because we want to test if CourseDurationLimitConfig is working correctly.
        mock_get_course_run_details.return_value = {'weeks_to_complete': 4}
        result = NotificationFilter.filter_audit_expired(
            [self.user.id, self.user_1.id],
            self.course,
        )
        self.assertEqual([self.user.id, self.user_1.id], result)

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    @mock.patch("openedx.core.djangoapps.notifications.filters.NotificationFilter.filter_audit_expired")
    def test_apply_filter(
        self,
        mock_filter_audit_expired,
        mock_get_course_run_details,
    ):
        """
        Test if apply_filter function is working correctly
        """
        mock_get_course_run_details.return_value = {'weeks_to_complete': 4}
        mock_filter_audit_expired.return_value = [self.user.id, self.user_1.id]
        result = NotificationFilter().apply_filters(
            [self.user.id, self.user_1.id],
            self.course.id,
            'new_comment_on_response'
        )
        self.assertEqual([self.user.id, self.user_1.id], result)
        mock_filter_audit_expired.assert_called_once()


def assign_enrollment_mode_to_users(course_id, users, mode):
    """
    Helper function to create an enrollment with the given mode.
    """
    for user in users:
        enrollment = CourseEnrollmentFactory.create(user=user, course_id=course_id)
        enrollment.mode = mode
        enrollment.save()

def assign_role_to_users(course_id, users, role_name):
    """
    Helper function to assign a role to a user.
    """
    role = Role.objects.create(name=role_name, course_id=course_id)
    role.users.set(users)
    role.save()

@ddt.ddt
class TestEnrollmentAudienceFilter(ModuleStoreTestCase):
    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        self.course = CourseFactory()
        self.enrollments = []
        self.students = [UserFactory() for _ in range(30)]

        # Create 10 audit enrollments
        assign_enrollment_mode_to_users(self.course.id, self.students[:10], CourseMode.AUDIT)

        # Create 10 honor enrollments
        assign_enrollment_mode_to_users(self.course.id, self.students[10:20], CourseMode.HONOR)

        # Create 10 verified enrollments
        assign_enrollment_mode_to_users(self.course.id, self.students[20:], CourseMode.VERIFIED)

    @ddt.data(
        (["audit"], 10),
        (["audit", "honor"], 20),
        (["audit", "honor", "verified"], 30),
        (["honor"], 10),
        (["honor", "verified"], 20),
        (["verified"], 10),
    )
    @ddt.unpack
    def test_valid_enrollment_filter(self, enrollment_modes, expected_count):
        course_key = self.course.id
        enrollment_filter = EnrollmentAudienceFilter(course_key)
        filtered_users = enrollment_filter.filter(enrollment_modes)
        self.assertEqual(len(filtered_users), expected_count)

    def test_invalid_enrollment_filter(self):
        # TODO: what to do when there are no results from filter
        #  VS when there are no valid enrollment filters
        #  VS no filters provided?
        course_key = "your_course_key"
        enrollment_filter = EnrollmentAudienceFilter(course_key)
        enrollment_modes = ["INVALID_MODE"]
        filtered_users = enrollment_filter.filter(enrollment_modes)
        self.assertIsNone(filtered_users)

@ddt.ddt
class TestRoleAudienceFilter(ModuleStoreTestCase):
    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        self.course = CourseFactory()
        self.enrollments = []
        self.students = [UserFactory() for _ in range(25)]

        # Assign 5 users with administrator role
        assign_role_to_users(self.course.id, self.students[:5], FORUM_ROLES[0])

        # Assign 5 users with moderator role
        assign_role_to_users(self.course.id, self.students[5:10], FORUM_ROLES[1])

        # Assign 5 users with student role
        assign_role_to_users(self.course.id, self.students[10:15], FORUM_ROLES[2])

        # Assign 5 users with community TA role
        assign_role_to_users(self.course.id, self.students[15:20], FORUM_ROLES[3])

        # Assign 5 users with group moderator role
        assign_role_to_users(self.course.id, self.students[20:25], FORUM_ROLES[4])

    @ddt.data(
        (["Administrator"], 5),
        (["Moderator"], 5),
        (["Student"], 5),
        (["Community TA"], 5),
        (["Group Moderator"], 5),
        (["Administrator", "Moderator"], 10),
        (["Administrator", "Moderator", "Student"], 15),
        (["Moderator", "Student", "Community TA"], 15),
        (["Student", "Community TA", "Group Moderator"], 15),
        (["Community TA", "Group Moderator"], 10),
        (["Administrator", "Moderator", "Student", "Community TA", "Group Moderator"], 25),
    )
    @ddt.unpack
    def test_valid_role_filter(self, role_names, expected_count):
        course_key = self.course.id
        role_filter = RoleAudienceFilter(course_key)
        filtered_users = role_filter.filter(role_names)
        self.assertEqual(len(filtered_users), expected_count)

    def test_invalid_role_filter(self):
        course_key = "your_course_key"
        role_filter = RoleAudienceFilter(course_key)
        role_names = ["INVALID_MODE"]
        filtered_users = role_filter.filter(role_names)
        self.assertIsNone(filtered_users)


class TestAudienceFilter(ModuleStoreTestCase):
    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        self.course = CourseFactory()
        self.enrollments = []
        self.students = [UserFactory() for _ in range(30)]

        # Create 10 audit enrollments
        assign_enrollment_mode_to_users(self.course.id, self.students[:10], CourseMode.AUDIT)

        # Create 10 honor enrollments
        assign_enrollment_mode_to_users(self.course.id, self.students[10:20], CourseMode.HONOR)

        # Create 10 verified enrollments
        assign_enrollment_mode_to_users(self.course.id, self.students[20:], CourseMode.VERIFIED)

        # Assign 5 users with administrator role
        assign_role_to_users(self.course.id, self.students[:5], FORUM_ROLES[0])

        # Assign 5 users with moderator role
        assign_role_to_users(self.course.id, self.students[5:10], FORUM_ROLES[1])

        # Assign 5 users with student role
        assign_role_to_users(self.course.id, self.students[10:15], FORUM_ROLES[2])

        # Assign 5 users with community TA role
        assign_role_to_users(self.course.id, self.students[15:20], FORUM_ROLES[3])

        # Assign 5 users with group moderator role
        assign_role_to_users(self.course.id, self.students[20:25], FORUM_ROLES[4])

    def test_combination_of_audience_filters(self):
        # TODO: Add more combinations here, also add note that the expected counts are dependant on the test setup
        audience_filters = {
            "enrollment": ["audit", "verified"],
            "role": ["Administrator", "Moderator"],
        }
        user_ids = calculate_course_wide_notification_audience(self.course.id, audience_filters)
        self.assertEqual(len(user_ids), 15)

    def test_empty_audience_filter(self):
        audience_filters = {}
        user_ids = calculate_course_wide_notification_audience(self.course.id, audience_filters)
        self.assertEqual(len(user_ids), 30)
