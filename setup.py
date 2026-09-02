"""Compatibility shim for editable installs with older versions of pip."""

from setuptools import find_packages, setup


setup(
    name="mobile-automation",
    version="0.1.0",
    description="A lightweight Python toolkit for Android automation through ADB.",
    packages=(
        find_packages("src")
        + find_packages(
            ".",
            include=[
                "utils", "utils.*", "test_script", "test_script.*",
                "page_objects", "page_objects.*",
            ],
        )
    ),
    package_dir={"mobile_automation": "src/mobile_automation"},
    python_requires=">=3.8",
    install_requires=["Pillow>=8,<11", "rapidocr-onnxruntime>=1.4,<2"],
    entry_points={"console_scripts": ["mobile-auto=mobile_automation.cli:main"]},
)
