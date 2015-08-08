# -*- coding: utf-8 -*-
from setuptools import setup, find_packages
import os

version = '0.0.1'

setup(
    name='fedex_shipment',
    version=version,
    description='The application to provide shipments with Fedex',
    author='olhonko',
    author_email='olhonko@gmail.com',
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=("frappe",),
)
