#!/usr/bin/python3

from glob import glob
from setuptools import setup


setup(
	name='eth-sanity',
	version='0.1a0',
	author='Guillermo Rodriguez',
	author_email='guillermo@telos.net',
	packages=['eth_sanity'],
	install_requires=[
        'web3',
        'trio',
        'httpx',
        'eth_abi',
        'eth_utils',
        'eth_typing'
    ],
    data_files=glob('abis/**')
)
