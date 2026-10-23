"""The JSON body Alertmanager sends to a webhook receiver (format version 4).

Reference: https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
Unknown fields are ignored, so a newer Alertmanager with extra fields still works.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Alert(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(alias="startsAt")
    # Alertmanager sends 0001-01-01T00:00:00Z while an alert is still firing.
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    generator_url: str = Field(default="", alias="generatorURL")
    fingerprint: str

    @property
    def name(self) -> str:
        return self.labels.get("alertname", "unknown")


class Notification(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    version: str
    group_key: str = Field(alias="groupKey")
    status: Literal["firing", "resolved"]
    receiver: str
    group_labels: dict[str, str] = Field(default_factory=dict, alias="groupLabels")
    common_labels: dict[str, str] = Field(default_factory=dict, alias="commonLabels")
    common_annotations: dict[str, str] = Field(default_factory=dict, alias="commonAnnotations")
    external_url: str = Field(default="", alias="externalURL")
    alerts: list[Alert] = Field(min_length=1)

    @property
    def title(self) -> str:
        """A short name for the incident: the alert name, if all alerts in the group share one."""
        return (
            self.common_labels.get("alertname")
            or self.group_labels.get("alertname")
            or self.alerts[0].name
        )
