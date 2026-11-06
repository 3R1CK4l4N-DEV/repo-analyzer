from django.views import View
from django.views.generic import ListView
from rest_framework.viewsets import ModelViewSet

from .models import Owner, Pet


class OwnerList(ListView):
    model = Owner


class AdoptView(View):
    def post(self, request, pk):
        owner = Owner.objects.get(pk=pk)
        if not owner.can_adopt():
            raise ValueError("too many pets")
        return None

    def get(self, request, pk):
        return None


class PetViewSet(ModelViewSet):
    queryset = Pet.objects.all()


def health(request):
    return None
