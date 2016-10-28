from rowboat.models.base import BaseModel, BigIntegerField, CharField


class Guild(BaseModel):
    id = BigIntegerField()
    config_url = CharField()
