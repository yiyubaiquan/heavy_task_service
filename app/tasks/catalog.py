from dataclasses import dataclass


@dataclass(frozen=True)
class TaskDefinition:
    task_type: str
    celery_name: str
    description: str


TASK_DEFINITIONS: dict[str, TaskDefinition] = {
    "mineru.pdf.parse": TaskDefinition(
        task_type="mineru.pdf.parse",
        celery_name="mineru.pdf.parse",
        description="Parse a PDF with MinerU and write artifacts to storage/outputs.",
    ),
}


def get_task_definition(task_type: str) -> TaskDefinition:
    try:
        return TASK_DEFINITIONS[task_type]
    except KeyError as exc:
        supported = ", ".join(sorted(TASK_DEFINITIONS))
        raise ValueError(f"unsupported task_type={task_type!r}; supported: {supported}") from exc
