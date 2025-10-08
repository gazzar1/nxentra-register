from django.contrib.auth import authenticate
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Company, User


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        exclude = ("owner", "id")


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "name")


class RegistrationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    name = serializers.CharField(max_length=150)
    password = serializers.CharField(min_length=8, write_only=True)
    company_name = serializers.CharField(max_length=10)
    currency = serializers.CharField(max_length=3)
    language = serializers.ChoiceField(choices=("en", "ar"))
    periods = serializers.IntegerField(min_value=1, max_value=24)
    current_period = serializers.IntegerField(min_value=1)
    thousand_separator = serializers.CharField(max_length=1, allow_blank=True)
    decimal_places = serializers.IntegerField(min_value=0, max_value=4)
    decimal_separator = serializers.ChoiceField(choices=(".", ","))
    date_format = serializers.ChoiceField(choices=("dd/mm/yyyy", "mm/dd/yyyy", "yyyy/mm/dd"))

    def validate_company_name(self, value: str):
        if " " in value:
            raise serializers.ValidationError("Use one word without spaces.")
        if not value.isalnum():
            raise serializers.ValidationError("Only letters and numbers are allowed.")
        normalized = value.lower()
        if Company.objects.filter(name=normalized).exists():
            raise serializers.ValidationError("Company identifier already exists.")
        return normalized

    def validate_currency(self, value: str):
        return value.upper()

    def validate_thousand_separator(self, value: str):
        allowed = {"", ",", ".", "none"}
        if value not in allowed:
            raise serializers.ValidationError("Invalid thousand separator")
        return "" if value == "none" else value

    def validate(self, attrs):
        if attrs["current_period"] > attrs["periods"]:
            raise serializers.ValidationError("Current period cannot exceed total periods.")
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        password = validated_data.pop("password")
        company_name = validated_data.pop("company_name")

        user = User.objects.create_user(
            email=validated_data["email"],
            name=validated_data["name"],
            password=password,
        )

        Company.objects.create(
            owner=user,
            name=company_name,
            currency=validated_data["currency"],
            language=validated_data["language"],
            periods=validated_data["periods"],
            current_period=validated_data["current_period"],
            thousand_separator=validated_data["thousand_separator"],
            decimal_places=validated_data["decimal_places"],
            decimal_separator=validated_data["decimal_separator"],
            date_format=validated_data["date_format"],
        )
        return user

    def to_representation(self, instance):
        refresh = RefreshToken.for_user(instance)
        return {"access": str(refresh.access_token), "refresh": str(refresh)}


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = User.EMAIL_FIELD

    def validate(self, attrs):
        authenticate_kwargs = {
            self.username_field: attrs.get("email"),
            "password": attrs.get("password"),
        }
        user = authenticate(request=self.context.get("request"), **authenticate_kwargs)
        if not user:
            raise AuthenticationFailed("Invalid credentials")
        refresh = RefreshToken.for_user(user)
        return {"access": str(refresh.access_token), "refresh": str(refresh)}


class ProfileSerializer(serializers.Serializer):
    user = UserSerializer()
    company = CompanySerializer()

    @classmethod
    def from_user(cls, user: User):
        company = getattr(user, "company", None)
        if not company:
            raise serializers.ValidationError("User does not have an associated company")
        return cls(instance={"user": UserSerializer(user).data, "company": CompanySerializer(company).data})
