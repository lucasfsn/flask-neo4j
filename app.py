from flask import Flask, jsonify, request
from neo4j import GraphDatabase
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)

uri = os.getenv("URI")
user = os.getenv("USERNAME")
password = os.getenv("PASSWORD")

driver = GraphDatabase.driver(uri, auth=(user, password), database="neo4j")


def get_employees(tx, filterField, filterValue, sort):
    query = "MATCH (e:Employee)-[r]->(d:Department)"

    if filterField and not filterValue:
        return None

    if filterField and filterValue and not filterField == "position":
        query += f" WHERE e.{filterField}='{filterValue}'"
    if filterField and filterValue and filterField == "position":
        type = "MANAGES" if filterValue == "manager" else "WORKS_IN"
        query += f" WHERE TYPE(r)='{type}'"

    query += " RETURN e.firstName as firstName, e.lastName as lastName, e.age as age"

    if sort:
        query += f" ORDER BY {sort}"

    results = tx.run(query).data()
    employees = [
        {
            "firstName": result["firstName"],
            "lastName": result["lastName"],
            "age": result["age"],
        }
        for result in results
    ]
    return employees


@app.route("/employees", methods=["GET"])
def get_employees_route():
    filterField = request.args.get("filter", None)
    filterValue = request.args.get("value", None)
    sort = request.args.get("sort", None)

    with driver.session() as session:
        employees = session.read_transaction(
            get_employees,
            filterField=filterField,
            filterValue=filterValue,
            sort=sort,
        )

    if not employees:
        return jsonify({"message": "No employees were found or invalid parameters"})

    response = {"employees": employees}
    return jsonify(response)


def add_employee(tx, firstName, lastName, age):
    if firstName is None or lastName is None or age is None:
        return None

    exist = "MATCH (e:Employee {firstName: $firstName, lastName: $lastName, age: $age}) RETURN e"
    result = tx.run(exist, firstName=firstName, lastName=lastName, age=age)

    if result.single() is not None:
        return None

    add = "CREATE (e:Employee {firstName: $firstName, lastName: $lastName, age: $age})"

    tx.run(add, firstName=firstName, lastName=lastName, age=age)

    return True


@app.route("/employees", methods=["POST"])
def add_employee_route():
    firstName = request.json.get("firstName", None)
    lastName = request.json.get("lastName", None)
    age = request.json.get("age", None)

    with driver.session() as session:
        employee = session.write_transaction(add_employee, firstName, lastName, age)

    if employee is None:
        return jsonify({"message": "Employee already exists or fields are missing"})

    response = {"status": "success"}
    return jsonify(response)


def edit_employee(tx, id, firstName, lastName, age):
    params = {"id": id}

    result = tx.run("MATCH (e:Employee) WHERE id(e)=$id RETURN e", id=id).data()

    if not result:
        return None

    query = "MATCH (e:Employee) WHERE id(e)=$id SET"

    if firstName is not None:
        query += " e.firstName = $firstName, "
        params["firstName"] = firstName
    if lastName is not None:
        query += " e.lastName = $lastName, "
        params["lastName"] = lastName
    if age is not None:
        query += " e.age = $age, "
        params["age"] = age

    query = query.rstrip(", ")

    query += " RETURN e"

    tx.run(query, **params)

    return jsonify({"id": id})


@app.route("/employees/<int:id>", methods=["PUT"])
def edit_employee_route(id):
    firstName = request.json.get("firstName", None)
    lastName = request.json.get("lastName", None)
    age = request.json.get("age", None)

    with driver.session() as session:
        employee = session.write_transaction(
            edit_employee, id, firstName, lastName, age
        )

    if not employee:
        response = {"message": "Employee not found"}
        return jsonify(response)

    response = {"status": "success"}
    return jsonify(response)


def delete_employee(tx, id):
    result = tx.run("MATCH (e:Employee) WHERE id(e)=$id RETURN e", id=id).data()

    if not result:
        return None

    isManager = tx.run(
        "MATCH (e:Employee)-[r:MANAGES]->(d:Department) WHERE id(e)=$id RETURN e", id=id
    )

    if not isManager.peek():
        tx.run("MATCH (e:Employee) WHERE id(e)=$id DETACH DELETE e", id=id)
        return True

    delete = "MATCH (e:Employee)-[r:MANAGES]->(d:Department) WHERE id(e)=$id DETACH DELETE e,d"
    tx.run(delete, id=id)
    return True


@app.route("/employees/<int:id>", methods=["DELETE"])
def delete_employee_route(id):
    with driver.session() as session:
        employee = session.write_transaction(delete_employee, id)

    if not employee:
        response = {"message": "Employee not found"}
        return jsonify(response), 404

    response = {"status": "success"}
    return jsonify(response)


def get_subordinates(tx, id):
    query = "MATCH (m:Employee)-[:MANAGES]->(d:Department)<-[:WORKS_IN]-(s:Employee) WHERE id(m)=$id AND NOT (s)-[:MANAGES]->(d) RETURN s.firstName as firstName, s.lastName as lastName, s.age as age"
    results = tx.run(query, id=id).data()
    subordinates = [
        {
            "firstName": result["firstName"],
            "lastName": result["lastName"],
            "age": result["age"],
        }
        for result in results
    ]
    return subordinates


@app.route("/employees/<int:id>/subordinates", methods=["GET"])
def get_subordinates_route(id):
    with driver.session() as session:
        employees = session.read_transaction(get_subordinates, id)

    if not employees:
        return jsonify({"message": "No subordinates were found"})

    response = {"employees": employees}
    return jsonify(response)


# TODO:
def get_departments(tx, filterField, filterValue, sort):
    query = "MATCH (d:Department)"

    possibleFields = tx.run(
        "MATCH (n:Department) UNWIND keys(n) AS fields RETURN DISTINCT fields"
    ).data()

    if filterField and not filterValue:
        return None

    if filterField and filterValue and filterField in possibleFields:
        query += f" WHERE d.{filterField}='{filterValue}'"

    query += " RETURN d.name as name"

    if sort:
        query += f" ORDER BY {sort}"

    results = tx.run(query).data()
    departments = [{"todo": result} for result in results]
    return departments


@app.route("/departments", methods=["GET"])
def get_departments_route():
    filterField = request.args.get("filter", None)
    filterValue = request.args.get("value", None)
    sort = request.args.get("sort", None)

    with driver.session() as session:
        departments = session.read_transaction(
            get_departments,
            filterField=filterField,
            filterValue=filterValue,
            sort=sort,
        )

    if not departments:
        return jsonify({"message": "No departments were found or invalid parameters"})

    response = {"departments": departments}
    return jsonify(response)


# -----


def get_department_employees(tx, id):
    query = "MATCH (e:Employee)-[:WORKS_IN]->(d:Department) WHERE id(d)=$id RETURN e.firstName as firstName, e.lastName as lastName, e.age as age"

    results = tx.run(query, id=id).data()
    employees = [
        {
            "firstName": result["firstName"],
            "lastName": result["lastName"],
            "age": result["age"],
        }
        for result in results
    ]
    return employees


@app.route("/departments/<int:id>/employees", methods=["GET"])
def get_department_employees_route(id):
    with driver.session() as session:
        employees = session.read_transaction(get_department_employees, id)

    if not employees:
        return jsonify({"message": "No employees were found"})

    response = {"employees": employees}
    return jsonify(response)


if __name__ == "__main__":
    app.run()
