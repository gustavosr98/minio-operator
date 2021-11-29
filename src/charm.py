#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from random import choices
from string import ascii_uppercase, digits

from oci_image import OCIImageResource, OCIImageResourceError
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from serialized_data_interface import (
    NoCompatibleVersions,
    NoVersionsListed,
    get_interfaces,
)


class Operator(CharmBase):
    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self.log = logging.getLogger()

        self._stored.set_default(secret_key=_gen_pass())

        self.image = OCIImageResource(self, "oci-image")

        for event in [
            self.on.config_changed,
            self.on.install,
            self.on.upgrade_charm,
            self.on["object-storage"].relation_changed,
            self.on["object-storage"].relation_joined,
        ]:
            self.framework.observe(event, self.main)

    def main(self, event):
        try:
            self._check_leader()

            interfaces = self._get_interfaces()

            image_details = self._check_image_details()

        except CheckFailed as error:
            self.model.unit.status = error.status
            return

        secret_key = self.model.config["secret-key"] or self._stored.secret_key
        self._send_info(interfaces, secret_key)

        self.model.unit.status = MaintenanceStatus("Setting pod spec")
        self.model.pod.set_spec(
            {
                "version": 3,
                "containers": [
                    {
                        "name": "minio",
                        "args": ["server", "/data"],
                        "imageDetails": image_details,
                        "ports": [
                            {
                                "name": "minio",
                                "containerPort": int(self.model.config["port"]),
                            }
                        ],
                        "envConfig": {
                            "MINIO_ACCESS_KEY": self.model.config["access-key"],
                            "MINIO_SECRET_KEY": secret_key,
                        },
                    }
                ],
            }
        )
        self.model.unit.status = ActiveStatus()

    def _check_leader(self):
        if not self.unit.is_leader():
            self.log.info("Not a leader, skipping set_pod_spec")
            raise CheckFailed("", ActiveStatus)

    def _get_interfaces(self):
        try:
            interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            raise CheckFailed(str(err), WaitingStatus)
        except NoCompatibleVersions as err:
            raise CheckFailed(str(err), BlockedStatus)
        return interfaces

    def _check_image_details(self):
        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            raise CheckFailed(f"{e.status_message}: oci-image", e.status_type)
        return image_details

    def _send_info(self, interfaces, secret_key):
        if interfaces["object-storage"]:
            interfaces["object-storage"].send_data(
                {
                    "access-key": self.model.config["access-key"],
                    "namespace": self.model.name,
                    "port": self.model.config["port"],
                    "secret-key": secret_key,
                    "secure": False,
                    "service": self.model.app.name,
                }
            )


def _gen_pass() -> str:
    return "".join(choices(ascii_uppercase + digits, k=30))


class CheckFailed(Exception):
    """ Raise this exception if one of the checks in main fails. """

    def __init__(self, msg, status_type=None):
        super().__init__()

        self.msg = msg
        self.status_type = status_type
        self.status = status_type(msg)


if __name__ == "__main__":
    main(Operator)
