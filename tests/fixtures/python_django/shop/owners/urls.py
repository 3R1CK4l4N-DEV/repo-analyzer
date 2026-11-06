from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("pets", views.PetViewSet)

urlpatterns = [
    path("owners/", views.OwnerList.as_view(), name="owner-list"),
    path("owners/<int:pk>/adopt/", views.AdoptView.as_view()),
    path("health/", views.health),
] + router.urls
