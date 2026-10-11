from setuptools import setup, find_packages

setup(
    name="RelayOS-ai-os",
    version="1.0.0",
    description="A deterministic, cost-aware Hybrid AI Operating System.",
    author="Rudra Malvankar",
    packages=find_packages(),
    install_requires=[
        "huggingface-hub",
        "python-dotenv",
        "llama-cpp-python",
        "pydantic",
        "httpx",
        "tenacity",
        "fastapi",
        "uvicorn",
        "openai",
        "anthropic",
    ],
    entry_points={
        "console_scripts": [
            "RelayOS=main:main"
        ]
    },
    python_requires=">=3.9",
)
