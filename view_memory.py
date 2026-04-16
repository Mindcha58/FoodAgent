from memory_store import meal_memory

results = meal_memory.get()

print("IDs:")
print(results["ids"])

print("\nMetadatas:")
print(results["metadatas"])

print("\nDocuments:")
print(results["documents"])
