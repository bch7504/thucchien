from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.db.models import Memory, User
from src.db.session import get_db
from src.models.memory_schemas import MemoryCreateRequest, MemoryOut, MemoryUpdateRequest

router = APIRouter()


def _to_out(memory: Memory) -> MemoryOut:
    return MemoryOut(
        id=memory.id, category=memory.category, title=memory.title, detail=memory.detail,
        created_at=memory.created_at,
    )


async def _get_own_memory_or_404(memory_id: str, current_user: User, db: AsyncSession) -> Memory:
    memory = (
        await db.execute(select(Memory).where(Memory.id == memory_id, Memory.owner_id == current_user.id))
    ).scalar_one_or_none()
    if memory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    return memory


@router.get("/memories", response_model=list[MemoryOut])
async def list_memories(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[MemoryOut]:
    memories = (
        await db.execute(
            select(Memory).where(Memory.owner_id == current_user.id).order_by(Memory.created_at.desc())
        )
    ).scalars().all()
    return [_to_out(m) for m in memories]


@router.post("/memories", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def create_memory(
    request: MemoryCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MemoryOut:
    memory = Memory(
        owner_id=current_user.id, category=request.category, title=request.title, detail=request.detail
    )
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return _to_out(memory)


@router.patch("/memories/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: str,
    request: MemoryUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MemoryOut:
    memory = await _get_own_memory_or_404(memory_id, current_user, db)
    for field, value in request.model_dump(exclude_unset=True).items():
        setattr(memory, field, value)
    await db.commit()
    await db.refresh(memory)
    return _to_out(memory)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    memory = await _get_own_memory_or_404(memory_id, current_user, db)
    await db.delete(memory)
    await db.commit()
