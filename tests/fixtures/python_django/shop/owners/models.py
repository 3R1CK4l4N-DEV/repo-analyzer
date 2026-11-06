from django.db import models


class Owner(models.Model):
    first_name = models.CharField(max_length=30)
    telephone = models.CharField(max_length=10)

    def can_adopt(self) -> bool:
        """Business rule: an owner may have at most 5 pets."""
        return self.pet_set.count() < 5


class Pet(models.Model):
    name = models.CharField(max_length=30)
    owner = models.ForeignKey(Owner, on_delete=models.CASCADE)
