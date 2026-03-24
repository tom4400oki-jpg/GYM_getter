import asyncio
import sys
import traceback
sys.path.insert(0, '.')

async def test():
    from backend.tasks import run_availability_check
    try:
        result = await run_availability_check('takatsu_sports_center', days_ahead=3)
        print('Result:', result)
    except Exception as e:
        print(f'ERROR type: {type(e).__name__}')
        print(f'ERROR msg: {repr(str(e))}')
        traceback.print_exc()

asyncio.run(test())
