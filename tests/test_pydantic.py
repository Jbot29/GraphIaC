from pydantic import BaseModel


class User(BaseModel):
    id: int
    name: str
    age: int

user1 = User(id=1, name="Alice", age=30)
user2 = User(id=1, name="Bob", age=35)



from deepdiff import DeepDiff
diff = DeepDiff(user1.model_dump(), user2.model_dump())

print(diff)

def hand_diff(a,b):
    differences = {}
    self_dict = a.dict()
    other_dict = b.dict()
        
    # Compare every field's value
    for field in self_dict:
        if self_dict[field] != other_dict[field]:
            differences[field] = (self_dict[field], other_dict[field])

                
def func(x):
    return x + 1


def test_answer():
    assert 5 == 5
