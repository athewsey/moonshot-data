import logging
from typing import Any

from moonshot.src.connectors.connector import Connector, perform_retry
from moonshot.src.connectors_endpoints.connector_endpoint_arguments import (
    ConnectorEndpointArguments,
)
from openai import AsyncAzureOpenAI, BadRequestError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AzureOpenAIConnector(Connector):
    def __init__(self, ep_arguments: ConnectorEndpointArguments):
        # Initialize super class
        super().__init__(ep_arguments)

        # Azure OpenAI has additional parameters
        self.api_version = self.optional_params.get("api_version", "2024-02-01")

        # Set OpenAI Key
        self._client = AsyncAzureOpenAI(
            api_key=self.token,
            # https://learn.microsoft.com/azure/ai-services/openai/reference#rest-api-versioning
            api_version=self.api_version,
            # https://learn.microsoft.com/azure/cognitive-services/openai/how-to/create-resource?pivots=web-portal#create-a-resource
            azure_endpoint=self.endpoint,
        )

        # Set the model to use and remove it from optional_params if it exists
        self.model = self.optional_params.get("model", "")

    @Connector.rate_limited
    @perform_retry
    async def get_response(self, prompt: str) -> str:
        """
        Asynchronously sends a prompt to the OpenAI API and returns the generated response.

        This method constructs a request with the given prompt, optionally prepended and appended with
        predefined strings, and sends it to the OpenAI API. If a system prompt is set, it is included in the
        request. The method then awaits the response from the API, processes it, and returns the resulting message
        content as a string.

        Args:
            prompt (str): The input prompt to send to the OpenAI API.

        Returns:
            str: The text response generated by the OpenAI model.
        """
        connector_prompt = f"{self.pre_prompt}{prompt}{self.post_prompt}"
        if self.system_prompt:
            openai_request = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": connector_prompt},
            ]
        else:
            openai_request = [{"role": "user", "content": connector_prompt}]

        # Merge self.optional_params with additional parameters
        new_params = {
            **self.optional_params,
            "model": self.model,
            "messages": openai_request,
            "timeout": self.timeout,
        }

        try:
            response = await self._client.chat.completions.create(**new_params)
            return await self._process_response(response)
        except BadRequestError as ex:
            # Azure OpenAI's Content Filter causes HTTP 400 errors when it detects inappropriate content
            if isinstance(ex.body, dict) and "innererror" in ex.body:
                if "code" in ex.body["innererror"]:
                    if (
                        "ResponsibleAIPolicyViolation" in ex.body["innererror"]["code"]
                        and "message" in ex.body
                    ):
                        # For this specific case, we want to continue processing the response as a model
                        # rejection, so we ignore the exception and return a valid looking response
                        return ex.body["message"]
            # Otherwise raise the exception
            raise

    async def _process_response(self, response: Any) -> str:
        """
        Process the response from OpenAI's API and return the message content as a string.

        This method processes the response received from OpenAI's API call, specifically targeting
        the chat completion response structure. It extracts the message content from the first choice
        provided in the response, which is expected to contain the relevant information or answer.

        Args:
            response (Any): The response object received from an OpenAI API call. It is expected to
            follow the structure of OpenAI's chat completion response.

        Returns:
            str: A string containing the message content from the first choice in the response. This
            content represents the AI-generated text based on the input prompt.
        """
        return response.choices[0].message.content
