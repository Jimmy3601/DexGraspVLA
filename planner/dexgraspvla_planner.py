import httpx
import json_repair
from openai import OpenAI

from planner.utils import parse_json, extract_list


class DexGraspVLAPlanner:
    def __init__(self,
                api_key: str = "EMPTY", 
                 # vllm server is started here
                base_url: str = "http://localhost:8000/v1",
                model_name: str = None):

        transport = httpx.HTTPTransport(retries=1)
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=httpx.Client(transport=transport)
        )
        self.model = self.client.models.list().data[0].id if model_name is None else model_name
    
    # classify prompt, decompose instructions, generate instructions, mark bbox, check grasp/instruction/user prompt complete
    def request_task(self,
            task_name: str,
            frame_path: str = None,
            instruction: str = None,
            max_token: int = 218
    ) -> str:
        if task_name == "classify_user_prompt":
            prompt = f"""
            Analyze the following user prompt: {instruction}

            User prompt types:
            - Type I (return True): User prompts with any specific descriptions
            Examples: 
            * Color-based: "green objects"
            * Position-based: "objects from the right"
            * Property-based: "all cups"
            * Combination: "the red cup on the left"

            - Type II (return False): Abstract prompts without any object descriptions
            Examples: "clear the table", "clean up", "remove everything"

            Please determine:
            - Is this a Type I prompt? (True/False)
            - Provide your reasoning

            Return format:
            True/False: your reasoning

            Examples:
            - "grab the green cup" -> True: Contains specific object (cup) and property (green)
            - "clear the table" -> False: No specific object characteristics mentioned
            """

        elif task_name == "decompose_user_prompt":
            prompt = f"""
            For user prompt: {instruction}
            Process:
            1. Analyze the user prompt and image together:
            - Match user prompt descriptions with visible objects in the image
            - If a description (e.g., "green objects") matches multiple objects, include all matching objects
            - Verify each mentioned object actually exists in the image

            2. Based on the robot arm's position (right edge of the screen) and table layout
            3. Determine the most efficient grasping sequence
            4. Generate a reordered list of objects to grasp
            
            Requirements:
            - Only include objects mentioned in the original user prompt
            - Keep position information for each object
            - Return as a list, ordered by grasping sequence

            Expected output format:
            ["object with position 1", "object with position 2", ...]
            """

        elif task_name == "generate_instruction":
            prompt = f"""
            Analyze the current desktop layout and select the most suitable object to grasp, considering the following factors:

            Grasping Strategy:
            1. The robotic arm is positioned on the far right (outside the frame)
            2. Grasping Priority Order:
               - Prioritize objects on the right to avoid knocking over other objects during later operations
               - Then consider objects in the middle
               - Finally, consider objects on the left
            3. Accessibility Analysis:
               - Relative positions between objects
               - Potential obstacles
               - Whether the grasping path might interfere with other objects

            Please provide your response in the following JSON format:
            {{
                "analysis": {{
                    "priority_consideration": "Explanation of why this object has priority",
                    "accessibility": "Analysis of object's accessibility",
                    "risk_assessment": "Potential risks in grasping this object"
                }},
                "target": "A comprehensive description of the target object 
                (e.g., 'the blue cube on the far right of the desktop, next to the red cylinder')"
            }}

            Ensure the output is in valid JSON format.
            Note: The 'target' field should ONLY contain the object's color, shape, and position in a natural, flowing sentence. Do not include any analysis or reasoning in this field.
            """

        elif task_name == "mark_bounding_box":
            prompt = f"""
            Analyze the image and identify the best matching object with the description: {instruction}.
            Instructions for object analysis:
            1. Select ONE object that best matches the description
            2. For the selected object, provide:
            - A concise label, object name (3-4 words max)
            - A detailed description (position, color, shape, context)
            - Accurate bbox coordinates

            Required JSON format with an example:
            ```json
            {{
                "bbox_2d": [x1, y1, x2, y2],
                "label": "green cup",  # Keep this very brief (3-4 words)
                "description": "A cylindrical green ceramic cup located on the right side of the wooden table, next to the laptop"  # Detailed description
            }}
            ```

            Critical requirements:
            - Return EXACTLY ONE object
            - "label": Must be brief (3-4 words) for quick reference
            - "description": Must be detailed and include spatial context
            - Use single JSON object format, not an array
            - Ensure bbox coordinates are within image boundaries
            """

        elif task_name == "check_grasp_success":
            prompt = f"""
            Analyze the image and determine if the robotic arm has successfully grasped an object:
            1. Observe the spatial relationship between the robotic hand and the object
            2. Output format: explain your reasoning, then conclude with a boolean value (True=grasped, False=not grasped)
            """
        
        elif task_name == "check_instruction_complete":
            prompt = f"""
            Please check whether {instruction} exists on the desktop. If it does not exist, output True; otherwise, output False.
            """
        
        elif task_name == "check_user_prompt_complete":
            prompt = """
            Please analyze the table in the image:

            Requirements:
            - Only detect physical objects with noticeable height/thickness (3D objects)
            - Exclude from consideration:
            * Flat items (papers, tablecloths, mats)
            * Light projections
            * Shadows
            * Surface patterns or textures

            Return format:
            - True: if the table is empty of 3D objects
            - False: if there are any 3D objects, followed by their names

            Example responses:
            True  (for empty table)
            False: cup, bottle, plate  (for table with objects)
            """

        else:
            raise ValueError(f"The task_name {task_name} is not a valid task name.")

        messages = [
            {
                "role": "system",
                "content": [{"type": "text", "text": "You are a helpful assistant."}],
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}]
            }
        ]
        if frame_path is not None:
            messages[1]["content"].append({
                "type": "image_url",
                "image_url": {"url": frame_path}
            })

        '''
        vLLM supports a set of parameters that are not part of the OpenAI API. In order to use them, you can pass them as extra parameters in the OpenAI client. Or directly merge them into the JSON payload if you are using HTTP call directly.
        extra_body, extra_header

        browse https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html#chat-template for more details
        
        use_beam_search: bool = False
        top_k: Optional[int] = None
        min_p: Optional[float] = None
        repetition_penalty: Optional[float] = None
        length_penalty: float = 1.0
        stop_token_ids: Optional[list[int]] = Field(default_factory=list)
        include_stop_str_in_output: bool = False
        ignore_eos: bool = False
        min_tokens: int = 0
        skip_special_tokens: bool = True
        spaces_between_special_tokens: bool = True
        truncate_prompt_tokens: Optional[Annotated[int, Field(ge=1)]] = None
        allowed_token_ids: Optional[list[int]] = None
        prompt_logprobs: Optional[int] = None

        add_special_tokens: bool = Field(
        default=True,
        description=(
            "If true (the default), special tokens (e.g. BOS) will be added to "
            "the prompt."),
    )
    response_format: Optional[ResponseFormat] = Field(
        default=None,
        description=
        ("Similar to chat completion, this parameter specifies the format of "
         "output. Only {'type': 'json_object'}, {'type': 'json_schema'} or "
         "{'type': 'text' } is supported."),
    )
    guided_json: Optional[Union[str, dict, BaseModel]] = Field(
        default=None,
        description="If specified, the output will follow the JSON schema.",
    )
    guided_regex: Optional[str] = Field(
        default=None,
        description=(
            "If specified, the output will follow the regex pattern."),
    )
    guided_choice: Optional[list[str]] = Field(
        default=None,
        description=(
            "If specified, the output will be exactly one of the choices."),
    )
    guided_grammar: Optional[str] = Field(
        default=None,
        description=(
            "If specified, the output will follow the context free grammar."),
    )
    guided_decoding_backend: Optional[str] = Field(
        default=None,
        description=(
            "If specified, will override the default guided decoding backend "
            "of the server for this specific request. If set, must be one of "
            "'outlines' / 'lm-format-enforcer'"))
    guided_whitespace_pattern: Optional[str] = Field(
        default=None,
        description=(
            "If specified, will override the default whitespace pattern "
            "for guided json decoding."))
    priority: int = Field(
        default=0,
        description=(
            "The priority of the request (lower means earlier handling; "
            "default: 0). Any priority other than 0 will raise an error "
            "if the served model does not use priority scheduling."))
    logits_processors: Optional[LogitsProcessors] = Field(
        default=None,
        description=(
            "A list of either qualified names of logits processors, or "
            "constructor objects, to apply when sampling. A constructor is "
            "a JSON object with a required 'qualname' field specifying the "
            "qualified name of the processor class/factory, and optional "
            "'args' and 'kwargs' fields containing positional and keyword "
            "arguments. For example: {'qualname': "
            "'my_module.MyLogitsProcessor', 'args': [1, 2], 'kwargs': "
            "{'param': 'value'}}."))
    return_tokens_as_token_ids: Optional[bool] = Field(
        default=None,
        description=(
            "If specified with 'logprobs', tokens are represented "
            " as strings of the form 'token_id:{token_id}' so that tokens "
            "that are not JSON-encodable can be identified."))
        '''

        chat_completion = self.client.chat.completions.create(
            model=self.model,
            max_completion_tokens=max_token,
            messages=messages
        )

        response = chat_completion.choices[0].message.content
        response_lower = response.lower()

        if task_name == "classify_user_prompt":
            if 'true' in response_lower:
                return "TypeI"
            elif 'false' in response_lower:
                return "TypeII"
            else:
                raise ValueError(f"The output text {response} is in the wrong format.")
        elif task_name == "decompose_user_prompt":
            list_output = extract_list(response)
            if type(list_output) == list:
                return list_output
            else:
                raise ValueError(f"The output text {list_output} is not a valid list.")
        elif task_name == "generate_instruction":
            generate_task_str = parse_json(response)
            generate_task_json = json_repair.loads(generate_task_str)
            generate_task = generate_task_json['target']
            if type(generate_task) == str:
                return generate_task
            else:
                raise ValueError(f"The output text {generate_task} is not a valid string.")
        elif task_name == "mark_bounding_box":
            bbox_str = parse_json(response)
            bbox_json = json_repair.loads(bbox_str)
            return bbox_json
        else:
            if 'true' in response_lower:
                return True
            elif 'false' in response_lower:
                return False
            else:
                raise ValueError(f"The output text {response} does not contain a valid boolean value.")
